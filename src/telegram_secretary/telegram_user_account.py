from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from telegram_secretary.config import AppConfig


class TelegramUserConnectionState(str, Enum):
    DISABLED = "disabled"
    MISSING_SECRETS = "missing_secrets"
    MISSING_SESSION = "missing_session"
    READY_FOR_MANUAL_LOGIN = "ready_for_manual_login"
    READY = "ready"


@dataclass(frozen=True)
class TelegramUserStatus:
    enabled: bool
    client: str
    state: TelegramUserConnectionState
    session_dir_configured: bool
    session_present: bool
    missing_secret_names: tuple[str, ...]

    def safe_text(self) -> str:
        if self.state == TelegramUserConnectionState.DISABLED:
            return (
                "Личный Telegram не включен. User-account ingest выключен до отдельного "
                "подтверждения владельца."
            )
        if self.state == TelegramUserConnectionState.MISSING_SECRETS:
            missing = ", ".join(self.missing_secret_names)
            return f"Личный Telegram не подключен. Не хватает server-only секретов: {missing}."
        if self.state == TelegramUserConnectionState.MISSING_SESSION:
            return (
                "Личный Telegram ожидает ручной вход на сервере. Код Telegram и 2FA-пароль "
                "нельзя вводить в чат с ботом или Codex."
            )
        if self.state == TelegramUserConnectionState.READY_FOR_MANUAL_LOGIN:
            return (
                "Личный Telegram готов к ручной авторизации на сервере через Telethon, "
                "но ingest еще не активирован."
            )
        return "Личный Telegram session найден. Ingest включается только отдельным подтверждением."


class DialogMessageProvider(Protocol):
    def latest_messages(self, dialog_query: str, limit: int) -> "DialogReadResult":
        ...


@dataclass(frozen=True)
class DialogReadResult:
    dialog_found: bool
    messages: list[str]
    read_failed: bool = False


class EmptyDialogMessageProvider:
    def latest_messages(self, dialog_query: str, limit: int) -> DialogReadResult:
        return DialogReadResult(dialog_found=False, messages=[])


class TelethonDialogMessageProvider:
    """Read recent text messages from one matching personal dialog.

    This adapter is deliberately request-scoped: it does not subscribe to all
    incoming personal messages and it never sends a message through the user
    account.  It is used only after the owner asks the bot to inspect a dialog.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def latest_messages(self, dialog_query: str, limit: int) -> DialogReadResult:
        try:
            return self._read(dialog_query, limit)
        except Exception:
            # Do not leak Telegram or session errors into the bot conversation.
            return DialogReadResult(dialog_found=False, messages=[], read_failed=True)

    def _read(self, dialog_query: str, limit: int) -> DialogReadResult:
        import asyncio

        return asyncio.run(self._read_async(dialog_query, limit))

    async def _read_async(self, dialog_query: str, limit: int) -> DialogReadResult:
        from telethon import TelegramClient

        session_path = self.config.telegram_user_session_dir / "personal"
        query = dialog_query.casefold()
        async with TelegramClient(
            str(session_path),
            int(self.config.telegram_api_id),
            self.config.telegram_api_hash,
        ) as client:
            matched_entity = None
            async for dialog in client.iter_dialogs():
                name = (dialog.name or "").casefold()
                if query == name or query in name:
                    matched_entity = dialog.entity
                    break
            if matched_entity is None:
                return DialogReadResult(dialog_found=False, messages=[])

            messages: list[str] = []
            async for message in client.iter_messages(matched_entity, limit=limit):
                if isinstance(message.raw_text, str) and message.raw_text:
                    messages.append(message.raw_text)
            return DialogReadResult(dialog_found=True, messages=messages)


class TelegramUserAccountService:
    def __init__(
        self,
        config: AppConfig,
        provider: DialogMessageProvider | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or (
            TelethonDialogMessageProvider(config)
            if config.telegram_user_ingest_enabled
            else EmptyDialogMessageProvider()
        )

    def status(self) -> TelegramUserStatus:
        missing = self._missing_secret_names()
        session_present = has_telethon_session(self.config.telegram_user_session_dir)

        if not self.config.telegram_user_ingest_enabled:
            state = TelegramUserConnectionState.DISABLED
        elif missing:
            state = TelegramUserConnectionState.MISSING_SECRETS
        elif not session_present:
            state = TelegramUserConnectionState.MISSING_SESSION
        else:
            state = TelegramUserConnectionState.READY

        return TelegramUserStatus(
            enabled=self.config.telegram_user_ingest_enabled,
            client=self.config.telegram_user_client,
            state=state,
            session_dir_configured=bool(self.config.telegram_user_session_dir),
            session_present=session_present,
            missing_secret_names=tuple(missing),
        )

    def analyze_dialog(self, dialog_query: str) -> str:
        normalized_query = dialog_query.strip()
        if not normalized_query:
            return "Укажи человека после команды, например: /dialog Имя."

        status = self.status()
        if status.state != TelegramUserConnectionState.READY:
            return status.safe_text()

        result = self.provider.latest_messages(
            normalized_query,
            limit=self.config.telegram_user_analysis_max_messages,
        )
        if result.read_failed:
            return "Не удалось прочитать этот диалог. Попробуй команду ещё раз."
        if not result.dialog_found:
            return "Диалог с таким именем не найден. Укажи имя как в списке чатов Telegram."

        events = find_discussed_event_snippets(result.messages)
        if not events:
            return "Диалог найден. В последних сообщениях обсуждённых событий не найдено."
        return "Найдены возможные обсуждения событий. Нужна следующая итерация безопасного резюме."

    def _missing_secret_names(self) -> list[str]:
        missing: list[str] = []
        if not self.config.telegram_api_id:
            missing.append("TELEGRAM_API_ID")
        if not self.config.telegram_api_hash:
            missing.append("TELEGRAM_API_HASH")
        if not self.config.telegram_user_phone_number:
            missing.append("TELEGRAM_USER_PHONE_NUMBER")
        return missing


def prepare_telegram_user_session_dir(session_dir: Path) -> None:
    """Create the personal Telegram session directory with private permissions."""

    session_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(session_dir, 0o700)
    for child in session_dir.iterdir():
        if child.is_dir():
            os.chmod(child, 0o700)
        elif child.is_file():
            os.chmod(child, 0o600)


def has_telethon_session(session_dir: Path) -> bool:
    return any(path.is_file() for path in (session_dir / "personal.session",))


def find_discussed_event_snippets(messages: list[str]) -> list[str]:
    event_terms = (
        "встреч",
        "созвон",
        "звон",
        "митинг",
        "meeting",
        "call",
        "event",
        "calendar",
        "календар",
    )
    return [message for message in messages if any(term in message.lower() for term in event_terms)]
