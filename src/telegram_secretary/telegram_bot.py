from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from telegram_secretary.availability import AvailabilityService
from telegram_secretary.call_research import (
    CallResearchService,
    call_command_help,
    parse_business_call_argument,
    render_call_placement,
)
from telegram_secretary.config import AppConfig
from telegram_secretary.telegram_user_account import TelegramUserAccountService


OWNER_START_TEXT = (
    "Telegram Secretary is online in safe polling mode. "
    "Webhook, TLS, auto-replies, calendar, Craft and voice are disabled."
)

OWNER_TEST_TEXT = (
    "Telegram Secretary live polling check: service is connected. "
    "Webhook/TLS and auto-replies remain disabled."
)


@dataclass
class TelegramPollingState:
    enabled: bool = False
    running: bool = False
    bot_ok: bool = False
    bot_username: str | None = None
    last_ok_at: str | None = None
    last_update_at: str | None = None
    last_error: str | None = None
    processed_updates: int = 0
    owner_messages_seen: int = 0
    test_message_status: str = "not_attempted"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "running": self.running,
                "bot_ok": self.bot_ok,
                "bot_username": self.bot_username,
                "last_ok_at": self.last_ok_at,
                "last_update_at": self.last_update_at,
                "last_error": self.last_error,
                "processed_updates": self.processed_updates,
                "owner_messages_seen": self.owner_messages_seen,
                "test_message_status": self.test_message_status,
            }

    def update(self, **values: Any) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(self, key, value)


class TelegramBotPoller:
    def __init__(
        self,
        config: AppConfig,
        state: TelegramPollingState,
        call_service: CallResearchService | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.availability = AvailabilityService(config)
        self.telegram_user_account = TelegramUserAccountService(config)
        self.call_service = call_service
        self._offset: int | None = None
        self._stop = threading.Event()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="telegram-bot-poller", daemon=True)
        thread.start()
        return thread

    def run(self) -> None:
        self.state.update(enabled=True, running=True, last_error=None)
        try:
            self._initialize_bot()
            if self.config.telegram_send_startup_test_message:
                self._send_owner_test_message()
            while not self._stop.is_set():
                self._poll_once()
        finally:
            self.state.update(running=False)

    def _initialize_bot(self) -> None:
        while not self._stop.is_set():
            try:
                result = self._request_json("getMe")
                username = result.get("username") if isinstance(result, dict) else None
                self.state.update(
                    bot_ok=True,
                    bot_username=username,
                    last_ok_at=_now(),
                    last_error=None,
                )
                return
            except Exception as exc:
                self.state.update(
                    bot_ok=False,
                    last_error=_safe_error(exc, self.config.telegram_bot_token),
                )
                time.sleep(5)

    def _send_owner_test_message(self) -> None:
        try:
            self._request_json(
                "sendMessage",
                {
                    "chat_id": self.config.secretary_owner_telegram_id,
                    "text": OWNER_TEST_TEXT,
                    "disable_notification": "true",
                },
            )
            self.state.update(test_message_status="sent", last_ok_at=_now(), last_error=None)
        except Exception as exc:
            self.state.update(
                test_message_status=f"failed:{_safe_error(exc, self.config.telegram_bot_token)}",
                last_error=_safe_error(exc, self.config.telegram_bot_token),
            )

    def _poll_once(self) -> None:
        params: dict[str, str] = {
            "timeout": "25",
            "allowed_updates": json.dumps(["message"], separators=(",", ":")),
        }
        if self._offset is not None:
            params["offset"] = str(self._offset)

        try:
            updates = self._request_json("getUpdates", params)
        except Exception as exc:
            self.state.update(last_error=_safe_error(exc, self.config.telegram_bot_token))
            time.sleep(5)
            return

        if not isinstance(updates, list):
            self.state.update(last_error="unexpected getUpdates response")
            time.sleep(5)
            return

        if updates:
            self.state.update(last_update_at=_now())

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = update_id + 1
            try:
                self._handle_update(update)
            except Exception as exc:
                self.state.update(last_error=_safe_error(exc, self.config.telegram_bot_token))

        self.state.update(last_ok_at=_now(), last_error=None)

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return

        self.state.update(processed_updates=self.state.snapshot()["processed_updates"] + 1)
        chat = message.get("chat")
        sender = message.get("from")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(sender, dict) or not isinstance(text, str):
            return

        sender_id = str(sender.get("id", ""))
        if sender_id != self.config.secretary_owner_telegram_id:
            return

        self.state.update(owner_messages_seen=self.state.snapshot()["owner_messages_seen"] + 1)
        normalized = text.strip().lower()
        if normalized.startswith("/start"):
            self._request_json(
                "sendMessage",
                {"chat_id": str(chat["id"]), "text": OWNER_START_TEXT},
            )
        elif normalized.startswith("/status"):
            self._request_json(
                "sendMessage",
                {
                    "chat_id": str(chat["id"]),
                    "text": (
                        "Safe polling mode is active. Auto-replies, webhook/TLS, "
                        "Craft and voice are off."
                    ),
                },
            )
        elif normalized.startswith("/today"):
            self._request_json(
                "sendMessage",
                {
                    "chat_id": str(chat["id"]),
                    "text": self.availability.today_reply(),
                },
            )
        elif normalized.startswith("/free"):
            self._request_json(
                "sendMessage",
                {
                    "chat_id": str(chat["id"]),
                    "text": self.availability.free_today_reply(),
                },
            )
        elif normalized.startswith("/tgstatus"):
            self._request_json(
                "sendMessage",
                {
                    "chat_id": str(chat["id"]),
                    "text": self.telegram_user_account.status().safe_text(),
                },
            )
        elif normalized.startswith("/callhelp"):
            self._request_json(
                "sendMessage",
                {
                    "chat_id": str(chat["id"]),
                    "text": call_command_help(),
                },
            )
        elif normalized.startswith("/call"):
            self._handle_call_command(str(chat["id"]), text)
        elif normalized.startswith("/dialog") or normalized.startswith("/analyze"):
            query = _command_argument(text)
            self._request_json(
                "sendMessage",
                {
                    "chat_id": str(chat["id"]),
                    "text": self.telegram_user_account.analyze_dialog(query),
                },
            )

    def _handle_call_command(self, chat_id: str, text: str) -> None:
        if self.call_service is None:
            self._request_json(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "Звонки не инициализированы в этом процессе."
                    ),
                },
            )
            return

        parsed = parse_business_call_argument(_command_argument(text))
        if not parsed.is_valid:
            self._request_json("sendMessage", {"chat_id": chat_id, "text": parsed.error})
            return

        try:
            request = self.call_service.create_request_from_command(
                parsed,
                max_duration_seconds=self.config.voice_business_call_max_duration_seconds,
            )
            placement = self.call_service.place_call(request)
            reply = render_call_placement(request, placement)
        except Exception as exc:
            reply = (
                "Не удалось подготовить звонок: "
                + _safe_call_error(exc, self.config)
            )

        self._request_json("sendMessage", {"chat_id": chat_id, "text": reply})

    def _request_json(self, method: str, params: dict[str, str] | None = None) -> Any:
        body = urlencode(params or {}).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{self.config.telegram_bot_token}/{method}",
            data=body,
            headers={"User-Agent": "telegram-secretary/0.1"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=35) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(_telegram_error_message(detail)) from exc
        except URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc

        if not payload.get("ok"):
            raise RuntimeError(_telegram_error_message(json.dumps(payload, ensure_ascii=False)))
        return payload.get("result")


def should_start_polling(config: AppConfig) -> bool:
    return (
        config.telegram_bot_delivery_mode == "polling"
        and not config.telegram_webhook_enabled
        and bool(config.telegram_bot_token)
        and bool(config.secretary_owner_telegram_id)
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _telegram_error_message(payload: str) -> str:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return payload[:240]
    description = parsed.get("description")
    if isinstance(description, str):
        return description[:240]
    return "telegram api error"


def _safe_error(exc: Exception, token: str) -> str:
    text = str(exc)
    if token:
        text = text.replace(token, "<token>")
    return text[:240]


def _safe_call_error(exc: Exception, config: AppConfig) -> str:
    text = str(exc)
    for secret in (
        config.telegram_bot_token,
        config.twilio_auth_token,
        config.voice_webhook_secret,
        config.llm_worker_bearer_token,
        config.cloudflare_api_token,
        config.exolve_api_key,
    ):
        if secret:
            text = text.replace(secret, "<secret>")
    return text[:240]


def _command_argument(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return ""
    return parts[1].strip()
