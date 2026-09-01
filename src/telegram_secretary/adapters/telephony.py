from __future__ import annotations

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
