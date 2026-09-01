from __future__ import annotations

import json
import time
from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
    WorkerWhisperTranscriber,
    build_business_call_script,
    normalize_phone_e164,
)
from telegram_secretary.config import AppConfig
from telegram_secretary.voice import CallReminder, VoiceProvider


_EXOLVE_MAKE_VOICE_MESSAGE_URL = "https://api.exolve.ru/call/v1/MakeVoiceMessage"
_EXOLVE_VOICE_MESSAGE_INFO_URL = "https://api.exolve.ru/call/v1/GetInfo"
_EXOLVE_TRANSCRIPTION_URL = "https://api.exolve.ru/statistics/call-record/v1/GetTranscribation"
_EXOLVE_RECORDING_DOWNLOAD_URL = "https://api.exolve.ru/statistics/download"


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


class ExolveBusinessCallProvider:
    """MTS Exolve outbound voice-message provider for Russian phone tests."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def place_business_call(self, request: BusinessCallRequest) -> BusinessCallPlacement:
        self._validate_configured(request.phone_e164)
        payload = {
            "source": _exolve_phone_digits(self.config.exolve_source_phone),
            "destination": _exolve_phone_digits(request.phone_e164),
            "tts": {
                "text": _exolve_tts_text(build_business_call_script(request)),
                "voice": self.config.exolve_tts_voice,
                "lang": self.config.exolve_tts_lang,
                "volume": self.config.exolve_tts_volume,
                "speed": self.config.exolve_tts_speed,
                "emotion": self.config.exolve_tts_emotion,
            },
        }
        response = _exolve_post_json(
            _EXOLVE_MAKE_VOICE_MESSAGE_URL,
            self.config.exolve_api_key,
            payload,
            timeout_seconds=30.0,
        )
        call_id = response.get("call_id")
        if not isinstance(call_id, str | int) or not str(call_id):
            raise RuntimeError("Exolve response has no call_id.")
        provider_call_id = str(call_id)
        return BusinessCallPlacement(
            request_id=request.request_id,
            provider="exolve",
            status=BusinessCallStatus.QUEUED,
            provider_call_id=provider_call_id,
            message="Exolve accepted outbound voice-message call.",
        )

    def _validate_configured(self, phone_e164: str) -> None:
        missing = []
        if not self.config.exolve_api_key:
            missing.append("EXOLVE_API_KEY")
        if not self.config.exolve_source_phone:
            missing.append("EXOLVE_SOURCE_PHONE")
        if missing:
            raise RuntimeError("Missing Exolve business call settings: " + ", ".join(missing))

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


@dataclass(frozen=True)
class ExolveRecordingRef:
    call_id: str
    recording_url: str
    duration_seconds: int | None
    status: str
    transcription_text: str | None = None


class ExolveLiveTestRunner:
    """Place one Exolve call and poll until it can be post-processed."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def place_call_and_wait_for_recording(
        self,
        request: BusinessCallRequest,
        *,
        wait_seconds: int = 300,
        poll_interval_seconds: float = 5.0,
    ) -> ExolveRecordingRef:
        placement = ExolveBusinessCallProvider(self.config).place_business_call(request)
        call_id = _required_str(placement.provider_call_id, "Exolve call_id")
        deadline = time.monotonic() + wait_seconds
        failed_statuses = {"no_answer", "canceled", "busy", "rejected", "failed"}
        last_status = "queued"
        duration_seconds: int | None = None
        recording_url = _exolve_recording_url(call_id)

        while time.monotonic() < deadline:
            info = self.get_voice_message_info(call_id)
            last_status = str(info.get("status") or last_status).casefold()
            duration_seconds = _optional_int_value(info.get("duration")) or duration_seconds
            if last_status == "completed":
                return ExolveRecordingRef(
                    call_id=call_id,
                    recording_url=recording_url,
                    duration_seconds=duration_seconds,
                    status=last_status,
                    transcription_text=self.get_transcription_text(call_id, allow_missing=True),
                )
            if last_status in failed_statuses:
                raise RuntimeError(f"Exolve call ended without recording: {last_status}")
            time.sleep(poll_interval_seconds)

        raise RuntimeError(f"Timed out waiting for Exolve recording, last status: {last_status}.")

    def get_voice_message_info(self, call_id: str) -> dict[str, Any]:
        return _exolve_post_json(
            _EXOLVE_VOICE_MESSAGE_INFO_URL,
            self.config.exolve_api_key,
            {"call_id": call_id},
            timeout_seconds=20.0,
        )

    def get_transcription_text(self, call_id: str, *, allow_missing: bool = False) -> str:
        try:
            payload = _exolve_post_json(
                _EXOLVE_TRANSCRIPTION_URL,
                self.config.exolve_api_key,
                {"uid": int(call_id)},
                timeout_seconds=30.0,
            )
        except RuntimeError as exc:
            if allow_missing and _exolve_transcription_missing(exc):
                return ""
            raise
        return _exolve_transcription_text(payload)


def build_call_research_service(
    config: AppConfig,
    store: CallResearchStore | None = None,
) -> CallResearchService:
    provider_name = config.voice_business_call_provider.casefold()
    provider = DryRunBusinessCallProvider()
    if config.voice_business_calls_enabled and provider_name == "twilio":
        provider = TwilioBusinessCallProvider(config)
    if config.voice_business_calls_enabled and provider_name in {"exolve", "mts_exolve"}:
        provider = ExolveBusinessCallProvider(config)

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
        config.voice_recording_transcriber in {"cloudflare_worker", "cloudflare_worker_whisper"}
        and config.llm_worker_url
        and config.llm_worker_bearer_token
    ):
        transcriber = WorkerWhisperTranscriber(
            worker_url=config.llm_worker_url,
            bearer_token=config.llm_worker_bearer_token,
            twilio_account_sid=config.twilio_account_sid or None,
            twilio_auth_token=config.twilio_auth_token or None,
            recording_bearer_token=config.exolve_api_key or None,
        )
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
            recording_bearer_token=config.exolve_api_key or None,
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


def _exolve_recording_url(call_id: str) -> str:
    return f"{_EXOLVE_RECORDING_DOWNLOAD_URL}/{call_id}"


def _exolve_post_json(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "telegram-secretary/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw_response = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Exolve request failed {exc.code}: {detail[:240]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Exolve request failed: {exc.reason}") from exc

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Exolve response is not JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Exolve response is not an object.")
    return parsed


def _exolve_phone_digits(raw_phone: str) -> str:
    return normalize_phone_e164(raw_phone).lstrip("+")


def _exolve_tts_text(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= 1000:
        return compact
    return compact[:997].rstrip() + "..."


def _exolve_transcription_text(payload: dict[str, Any]) -> str:
    records = payload.get("transcribation") or payload.get("transcription")
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        return ""

    lines: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        chunks = record.get("chunks", [])
        if isinstance(chunks, dict):
            chunks = [chunks]
        if not isinstance(chunks, list):
            continue
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            role = _exolve_channel_role(chunk.get("channel_tag"))
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _exolve_channel_role(channel_tag: object) -> str:
    tag = str(channel_tag or "").strip()
    if tag == "1":
        return "Робот"
    if tag == "2":
        return "Собеседник"
    return "Участник"


def _exolve_transcription_missing(exc: RuntimeError) -> bool:
    text = str(exc).casefold()
    return "404" in text or "not found transcribation" in text or "not found transcription" in text


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


def _optional_int_value(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
