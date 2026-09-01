from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from telegram_secretary.config import AppConfig
from telegram_secretary.telegram_user_account import (
    DialogReadResult,
    TelegramUserAccountService,
    TelegramUserConnectionState,
    find_discussed_event_snippets,
    prepare_telegram_user_session_dir,
)


class FoundEmptyDialogProvider:
    def latest_messages(self, dialog_query: str, limit: int) -> DialogReadResult:
        return DialogReadResult(dialog_found=True, messages=[])


class TelegramUserAccountServiceTest(TestCase):
    def test_status_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = AppConfig.from_env()

        status = TelegramUserAccountService(config).status()

        self.assertEqual(status.state, TelegramUserConnectionState.DISABLED)
        self.assertIn("не включен", status.safe_text())

    def test_enabled_status_reports_missing_secrets_by_name_only(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_USER_INGEST_ENABLED": "true"}, clear=True):
            config = AppConfig.from_env()

        status = TelegramUserAccountService(config).status()

        self.assertEqual(status.state, TelegramUserConnectionState.MISSING_SECRETS)
        self.assertEqual(
            status.missing_secret_names,
            ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_USER_PHONE_NUMBER"),
        )
        self.assertNotIn("+10000000000", status.safe_text())

    def test_analyze_dialog_returns_nothing_found_for_ready_empty_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "telethon"
            session_dir.mkdir()
            (session_dir / "personal.session").write_text("", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "TELEGRAM_USER_INGEST_ENABLED": "true",
                    "TELEGRAM_API_ID": "123",
                    "TELEGRAM_API_HASH": "hash",
                    "TELEGRAM_USER_PHONE_NUMBER": "+10000000000",
                    "TELEGRAM_USER_SESSION_DIR": str(session_dir),
                },
                clear=True,
            ):
                config = AppConfig.from_env()

            reply = TelegramUserAccountService(
                config, provider=FoundEmptyDialogProvider()
            ).analyze_dialog("Alice")

            self.assertIn("не найдено", reply)

    def test_prepare_telegram_user_session_dir_uses_private_permissions(self) -> None:
        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "telethon"
            session_dir.mkdir()
            session_file = session_dir / "personal.session"
            session_file.write_text("", encoding="utf-8")
            os.chmod(session_file, 0o644)

            prepare_telegram_user_session_dir(session_dir)

            self.assertEqual(session_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(session_file.stat().st_mode & 0o777, 0o600)

    def test_find_discussed_event_snippets_is_keyword_based(self) -> None:
        matches = find_discussed_event_snippets(["просто привет", "давай созвон завтра"])

        self.assertEqual(matches, ["давай созвон завтра"])
