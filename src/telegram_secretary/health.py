from __future__ import annotations

import json
import sys
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from telegram_secretary import __version__
from telegram_secretary.adapters.telephony import recorded_twiml
from telegram_secretary.call_research import (
    CallExtraction,
    CallRecordingNotice,
    CallResearchService,
    render_call_extraction,
)
from telegram_secretary.config import AppConfig


StatusProvider = Callable[[], dict[str, Any]]


def health_payload(
    config: AppConfig,
    polling_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    polling = polling_status or {"enabled": False, "running": False}
    return {
        "status": "ok",
        "service": "telegram-secretary",
        "version": __version__,
        "runtime_mode": config.runtime_mode,
        "public_host": config.public_host,
        "tls_webhook_enabled": config.tls_webhook_enabled,
        "telegram_bot_delivery_mode": config.telegram_bot_delivery_mode,
        "telegram_webhook_enabled": config.telegram_webhook_enabled,
        "telegram_user_ingest_enabled": config.telegram_user_ingest_enabled,
        "telegram_user_client": config.telegram_user_client,
        "auto_reply_enabled": config.auto_reply_enabled,
        "calendar_provider": config.calendar_provider,
        "calendar_configured": _calendar_configured(config),
        "craft_source_mode": config.craft_source_mode,
        "voice_provider": config.voice_provider,
        "voice_business_calls_enabled": config.voice_business_calls_enabled,
        "voice_business_call_provider": config.voice_business_call_provider,
        "call_analysis_provider": config.call_analysis_provider,
        "voice_recording_transcriber": config.voice_recording_transcriber,
        "polling": polling,
    }


def readiness_payload(
    config: AppConfig,
    polling_status: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    missing = _missing_required_live_settings(config)
    polling = polling_status or {"enabled": False, "running": False, "bot_ok": False}
    polling_ready = (not polling.get("enabled")) or (
        bool(polling.get("running")) and bool(polling.get("bot_ok"))
    )
    if (config.runtime_mode == "dry_run" or not missing) and polling_ready:
        return (
            HTTPStatus.OK,
            {
                "status": "ready",
                "service": "telegram-secretary",
                "runtime_mode": config.runtime_mode,
                "missing_live_settings": missing,
                "polling": polling,
            },
        )
    return (
        HTTPStatus.SERVICE_UNAVAILABLE,
        {
            "status": "not_ready",
            "service": "telegram-secretary",
            "runtime_mode": config.runtime_mode,
            "missing_live_settings": missing,
            "polling": polling,
        },
    )


def serve_health(
    host: str,
    port: int,
    config: AppConfig,
    polling_status_provider: StatusProvider | None = None,
    call_service: CallResearchService | None = None,
) -> None:
    handler = _handler_for(config, polling_status_provider, call_service)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"telegram-secretary health server listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("telegram-secretary health server stopping", flush=True)
    finally:
        server.server_close()


def check_url(url: str, timeout_seconds: float = 3.0) -> int:
    request = Request(url, headers={"User-Agent": "telegram-secretary-healthcheck/0.1"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if HTTPStatus.OK <= response.status < HTTPStatus.MULTIPLE_CHOICES:
                return 0
            print(f"unhealthy status: {response.status}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        return 1


def _handler_for(
    config: AppConfig,
    polling_status_provider: StatusProvider | None = None,
    call_service: CallResearchService | None = None,
) -> type[BaseHTTPRequestHandler]:
    def polling_status() -> dict[str, Any]:
        if polling_status_provider is None:
            return {"enabled": False, "running": False}
        return polling_status_provider()

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._write_json(HTTPStatus.OK, health_payload(config, polling_status()))
                return

            if parsed.path == "/readyz":
                status, payload = readiness_payload(config, polling_status())
                self._write_json(status, payload)
                return

            if parsed.path == "/pollingz":
                status = (
                    HTTPStatus.OK
                    if polling_status().get("running")
                    else HTTPStatus.SERVICE_UNAVAILABLE
                )
                payload = {
                    "status": "ok" if status == HTTPStatus.OK else "not_ready",
                    "polling": polling_status(),
                }
                self._write_json(status, payload)
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"status": "not_found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            query_params = _query_params(parsed.query)
            if parsed.path == "/voice/business/recorded":
                if not _voice_token_ok(config, query_params):
                    self._write_json(HTTPStatus.FORBIDDEN, {"status": "forbidden"})
                    return
                self._write_xml(HTTPStatus.OK, recorded_twiml())
                return

            if parsed.path == "/voice/business/status":
                if not _voice_token_ok(config, query_params):
                    self._write_json(HTTPStatus.FORBIDDEN, {"status": "forbidden"})
                    return
                self._write_json(HTTPStatus.OK, {"status": "accepted"})
                return

            if parsed.path not in {"/voice/business/recording", "/voice/business/transcription"}:
                self._write_json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
                return

            if call_service is None:
                self._write_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"status": "not_ready", "reason": "call_service_not_configured"},
                )
                return
            if not _voice_token_ok(config, query_params):
                self._write_json(HTTPStatus.FORBIDDEN, {"status": "forbidden"})
                return

            try:
                body_params = _read_body_params(self)
            except Exception as exc:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "bad_request", "reason": str(exc)[:160]},
                )
                return
            params = {**body_params, **query_params}
            notice = _recording_notice_from_params(parsed.path, params)
            result = call_service.process_recording_notice(notice)
            payload: dict[str, Any] = {
                "status": result.status.value,
                "message": result.message,
            }
            if result.extraction is not None:
                payload["extraction"] = _safe_extraction_payload(result.extraction)
                telegram_text = render_call_extraction(result.extraction)
                payload["telegram_text"] = telegram_text
                if config.telegram_bot_token and config.secretary_owner_telegram_id:
                    payload["owner_notification"] = _send_owner_telegram_message(
                        config,
                        telegram_text,
                    )
            self._write_json(HTTPStatus.OK, payload)

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}", flush=True)

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_xml(self, status: int, xml: str) -> None:
            body = xml.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return HealthHandler


def _missing_required_live_settings(config: AppConfig) -> list[str]:
    required = {}
    if config.telegram_bot_delivery_mode == "polling":
        required["TELEGRAM_BOT_TOKEN"] = config.telegram_bot_token
        required["SECRETARY_OWNER_TELEGRAM_ID"] = config.secretary_owner_telegram_id
    provider_name = config.voice_business_call_provider.casefold()
    if config.voice_business_calls_enabled and provider_name == "twilio":
        required["TWILIO_ACCOUNT_SID"] = config.twilio_account_sid
        required["TWILIO_AUTH_TOKEN"] = config.twilio_auth_token
        required["TWILIO_FROM_PHONE_E164"] = config.twilio_from_phone_e164
        required["VOICE_WEBHOOK_BASE_URL"] = config.voice_webhook_base_url
        required["VOICE_WEBHOOK_SECRET"] = config.voice_webhook_secret
    if config.voice_business_calls_enabled and provider_name in {
        "exolve",
        "mts_exolve",
    }:
        required["EXOLVE_API_KEY"] = config.exolve_api_key
        required["EXOLVE_SOURCE_PHONE"] = config.exolve_source_phone
    if config.call_analysis_provider == "cloudflare_worker":
        required["LLM_WORKER_URL"] = config.llm_worker_url
        required["LLM_WORKER_BEARER_TOKEN"] = config.llm_worker_bearer_token
    if config.voice_recording_transcriber == "cloudflare_whisper":
        required["CLOUDFLARE_ACCOUNT_ID"] = config.cloudflare_account_id
        required["CLOUDFLARE_API_TOKEN"] = config.cloudflare_api_token
    return [name for name, value in required.items() if not value]


def _calendar_configured(config: AppConfig) -> bool:
    if config.calendar_provider not in {"icloud_caldav", "apple_caldav"}:
        return False
    return bool(config.apple_calendar_username and config.apple_calendar_app_password)


def _voice_token_ok(config: AppConfig, params: dict[str, str]) -> bool:
    if not config.voice_webhook_secret:
        return False
    return params.get("token") == config.voice_webhook_secret


def _read_body_params(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    raw_length = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw_length)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    if length > 1_000_000:
        raise RuntimeError("voice webhook body is too large")
    body = handler.rfile.read(length)
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" in content_type:
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            return {}
        return {str(key): str(value) for key, value in parsed.items() if value is not None}
    return _query_params(body.decode("utf-8", errors="replace"))


def _query_params(raw_query: str) -> dict[str, str]:
    return {
        key: values[-1]
        for key, values in parse_qs(raw_query, keep_blank_values=True).items()
        if values
    }


def _recording_notice_from_params(path: str, params: dict[str, str]) -> CallRecordingNotice:
    request_id = params.get("request_id", "")
    transcription_text = params.get("TranscriptionText") or params.get("transcription_text")
    recording_status = (
        params.get("RecordingStatus")
        or params.get("TranscriptionStatus")
        or params.get("recording_status")
        or params.get("status")
        or ("transcribed" if path.endswith("/transcription") else "completed")
    )
    return CallRecordingNotice(
        request_id=request_id,
        provider=params.get("provider", "twilio"),
        provider_call_id=params.get("CallSid") or params.get("call_sid") or params.get("call_id"),
        recording_url=params.get("RecordingUrl") or params.get("recording_url"),
        recording_status=recording_status,
        duration_seconds=_optional_int(
            params.get("RecordingDuration") or params.get("duration_seconds")
        ),
        transcription_text=transcription_text,
    )


def _optional_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _safe_extraction_payload(extraction: CallExtraction) -> dict[str, Any]:
    return {
        "request_id": extraction.request_id,
        "summary": extraction.summary,
        "facts": [
            {
                "name": fact.name,
                "value": fact.value,
                "confidence": fact.confidence,
            }
            for fact in extraction.facts
        ],
        "missing_items": extraction.missing_items,
        "next_actions": extraction.next_actions,
        "confidence": extraction.confidence,
        "analyzed_at": extraction.analyzed_at.isoformat(),
    }


def _send_owner_telegram_message(config: AppConfig, text: str) -> str:
    params = {
        "chat_id": config.secretary_owner_telegram_id,
        "text": text,
    }
    request = Request(
        f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
        data=urlencode(params).encode("utf-8"),
        headers={"User-Agent": "telegram-secretary/0.1"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return "failed:" + _redact(config.telegram_bot_token, detail[:160])
    except Exception as exc:
        return "failed:" + _redact(config.telegram_bot_token, str(exc)[:160])
    if not isinstance(payload, dict) or not payload.get("ok"):
        return "failed:telegram_api_error"
    return "sent"


def _redact(secret: str, text: str) -> str:
    if secret:
        return text.replace(secret, "<secret>")
    return text
