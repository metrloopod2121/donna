from __future__ import annotations

import time
from dataclasses import dataclass
from html import escape
from urllib.parse import urlencode

from telegram_secretary.call_research import (
    BusinessCallPlacement,
    BusinessCallRequest,
    BusinessCallStatus,
    CallResearchService,
    CallResearchStore,
    CloudflareWhisperTranscriber,
    CloudflareWorkerConversationAnalyzer,
    DryRunBusinessCallProvider,
    RuleBasedConversationAnalyzer,
    build_business_call_script,
    normalize_phone_e164,
)
from telegram_secretary.config import AppConfig
from telegram_secretary.voice import CallReminder, VoiceProvider


class TelephonyVoiceProvider(VoiceProvider):
    """Voice/SIP provider adapter for real calls.

    A first implementation can use Twilio or Vonage webhooks:
    inbound call -> STT -> secretary intent -> TTS response,
    outbound reminder -> owner phone only -> TTS reminder.
    """

    def answer_owner_call(self, prompt: str) -> str:
        raise NotImplementedError("Connect inbound telephony webhook and TTS response.")

    def place_owner_reminder_call(self, reminder: CallReminder) -> str:
        raise NotImplementedError("Connect outbound owner-only reminder calls.")


class TwilioBusinessCallProvider:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def place_business_call(self, request: BusinessCallRequest) -> BusinessCallPlacement:
        self._validate_configured(request.phone_e164)
        try:
            from twilio.rest import Client
        except ImportError as exc:
            raise RuntimeError(
                "Twilio SDK is not installed. Install telegram-secretary[voice]."
            ) from exc

        client = Client(self.config.twilio_account_sid, self.config.twilio_auth_token)
        call = client.calls.create(
            to=request.phone_e164,
            from_=normalize_phone_e164(self.config.twilio_from_phone_e164),
            twiml=build_twilio_business_call_twiml(request, self.config),
            status_callback=_callback_url(
                self.config,
                "/voice/business/status",
                request.request_id,
            ),
            status_callback_method="POST",
        )
        provider_call_id = getattr(call, "sid", None)
        return BusinessCallPlacement(
            request_id=request.request_id,
            provider="twilio",
            status=BusinessCallStatus.QUEUED,
            provider_call_id=provider_call_id if isinstance(provider_call_id, str) else None,
            message="Twilio accepted outbound business call.",
        )

    def _validate_configured(self, phone_e164: str) -> None:
        missing = []
        if not self.config.twilio_account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.config.twilio_auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.config.twilio_from_phone_e164:
            missing.append("TWILIO_FROM_PHONE_E164")
        if not self.config.voice_webhook_base_url:
            missing.append("VOICE_WEBHOOK_BASE_URL")
        if not self.config.voice_webhook_secret:
            missing.append("VOICE_WEBHOOK_SECRET")
        if missing:
            raise RuntimeError("Missing Twilio business call settings: " + ", ".join(missing))

        prefixes = self.config.voice_business_call_allowed_prefixes
        if prefixes and not any(phone_e164.startswith(prefix) for prefix in prefixes):
            allowed = ", ".join(sorted(prefixes))
            raise RuntimeError(f"Business calls are allowed only for prefixes: {allowed}")


@dataclass(frozen=True)
class TwilioRecordingRef:
    call_sid: str
    recording_sid: str
    recording_url: str
    duration_seconds: int | None


class TwilioLiveTestRunner:
    """Place one call and poll Twilio REST for the resulting recording.

    This bypasses public webhooks, so it is useful for a first real phone test
    from a developer shell: call self, speak after the beep, poll the recording,
    then send the audio to STT/post-processing.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def place_call_and_wait_for_recording(
        self,
        request: BusinessCallRequest,
        *,
        wait_seconds: int = 240,
        poll_interval_seconds: float = 5.0,
    ) -> TwilioRecordingRef:
        self._validate_configured()
        try:
            from twilio.rest import Client
        except ImportError as exc:
            raise RuntimeError(
                "Twilio SDK is not installed. Install telegram-secretary[voice]."
            ) from exc

        client = Client(self.config.twilio_account_sid, self.config.twilio_auth_token)
        call = client.calls.create(
            to=request.phone_e164,
            from_=normalize_phone_e164(self.config.twilio_from_phone_e164),
            twiml=build_twilio_live_test_twiml(request),
        )
        call_sid = _required_str(getattr(call, "sid", None), "Twilio call SID")
        deadline = time.monotonic() + wait_seconds
        terminal_statuses = {"busy", "failed", "no-answer", "canceled"}

        while time.monotonic() < deadline:
            recordings = client.recordings.list(call_sid=call_sid, limit=10)
            for recording in recordings:
                recording_sid = getattr(recording, "sid", None)
                recording_status = str(getattr(recording, "status", "") or "")
                if recording_sid and recording_status in {"completed", ""}:
                    return TwilioRecordingRef(
                        call_sid=call_sid,
                        recording_sid=str(recording_sid),
                        recording_url=_twilio_recording_url(
                            self.config.twilio_account_sid,
                            str(recording_sid),
                        ),
                        duration_seconds=_optional_recording_duration(recording),
                    )

            current_call = client.calls(call_sid).fetch()
            status = str(getattr(current_call, "status", "") or "")
            if status in terminal_statuses:
                raise RuntimeError(f"Twilio call ended without recording: {status}")
            time.sleep(poll_interval_seconds)

        raise RuntimeError("Timed out waiting for Twilio recording.")

    def _validate_configured(self) -> None:
        missing = []
        if not self.config.twilio_account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.config.twilio_auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.config.twilio_from_phone_e164:
            missing.append("TWILIO_FROM_PHONE_E164")
        if missing:
            raise RuntimeError("Missing Twilio live-test settings: " + ", ".join(missing))


def build_call_research_service(
    config: AppConfig,
    store: CallResearchStore | None = None,
) -> CallResearchService:
    provider = (
        TwilioBusinessCallProvider(config)
        if config.voice_business_calls_enabled and config.voice_business_call_provider == "twilio"
        else DryRunBusinessCallProvider()
    )

    fallback = RuleBasedConversationAnalyzer()
    analyzer = fallback
    if (
        config.call_analysis_provider == "cloudflare_worker"
        and config.llm_worker_url
        and config.llm_worker_bearer_token
    ):
        analyzer = CloudflareWorkerConversationAnalyzer(
            config.llm_worker_url,
            config.llm_worker_bearer_token,
            fallback=fallback,
        )

    transcriber = None
    if (
        config.voice_recording_transcriber == "cloudflare_whisper"
        and config.cloudflare_account_id
        and config.cloudflare_api_token
    ):
        transcriber = CloudflareWhisperTranscriber(
            account_id=config.cloudflare_account_id,
            api_token=config.cloudflare_api_token,
            model=config.cloudflare_whisper_model,
            twilio_account_sid=config.twilio_account_sid or None,
            twilio_auth_token=config.twilio_auth_token or None,
        )

    return CallResearchService(
        provider=provider,
        analyzer=analyzer,
        store=store,
        transcriber=transcriber,
    )


def build_twilio_business_call_twiml(request: BusinessCallRequest, config: AppConfig) -> str:
    script = build_business_call_script(request)
    recording_url = _callback_url(config, "/voice/business/recording", request.request_id)
    action_url = _callback_url(config, "/voice/business/recorded", request.request_id)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say language="ru-RU">{escape(script)}</Say>'
        "<Record "
        f'maxLength="{request.max_duration_seconds}" '
        'playBeep="true" '
        'trim="trim-silence" '
        f'recordingStatusCallback="{escape(recording_url, quote=True)}" '
        'recordingStatusCallbackMethod="POST" '
        'recordingStatusCallbackEvent="completed absent" '
        f'action="{escape(action_url, quote=True)}" '
        '/>'
        "</Response>"
    )


def build_twilio_live_test_twiml(request: BusinessCallRequest) -> str:
    script = build_business_call_script(request)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say language="ru-RU">{escape(script)}</Say>'
        "<Record "
        f'maxLength="{request.max_duration_seconds}" '
        'playBeep="true" '
        'trim="trim-silence" '
        'timeout="5" '
        "/>"
        '<Say language="ru-RU">'
        "Спасибо, я передам информацию Матвею."
        "</Say>"
        "<Hangup/>"
        "</Response>"
    )


def recorded_twiml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Say language="ru-RU">'
        "Спасибо, я передам информацию Матвею."
        "</Say>"
        "<Hangup/>"
        "</Response>"
    )


def _callback_url(config: AppConfig, path: str, request_id: str) -> str:
    base_url = config.voice_webhook_base_url.rstrip("/")
    query = urlencode({"request_id": request_id, "token": config.voice_webhook_secret})
    return f"{base_url}{path}?{query}"


def _twilio_recording_url(account_sid: str, recording_sid: str) -> str:
    return (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
        f"/Recordings/{recording_sid}"
    )


def _required_str(value: object, name: str) -> str:
    if isinstance(value, str) and value:
        return value
    raise RuntimeError(f"{name} is missing.")


def _optional_recording_duration(recording: object) -> int | None:
    duration = getattr(recording, "duration", None)
    if duration is None:
        return None
    try:
        return int(duration)
    except (TypeError, ValueError):
        return None
