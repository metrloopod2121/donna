from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from telegram_secretary.config import AppConfig
from telegram_secretary.telethon_login import (
    check_runtime,
    login_preflight,
    secure_telethon_session_files,
    telethon_session_path,
)


class TelethonLoginTest(TestCase):
    def test_preflight_requires_secret_names_and_ingest_disabled(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_USER_INGEST_ENABLED": "true"}, clear=True):
            config = AppConfig.from_env()

        preflight = login_preflight(config)

        self.assertFalse(preflight.ok)
        self.assertEqual(
            preflight.missing_secret_names,
            ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_USER_PHONE_NUMBER"),
        )
        self.assertTrue(preflight.ingest_enabled)

    def test_session_path_is_inside_configured_session_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"TELEGRAM_USER_SESSION_DIR": tmp}, clear=True):
                config = AppConfig.from_env()

            self.assertEqual(telethon_session_path(config), Path(tmp) / "personal")

    def test_secure_session_files_uses_private_permissions(self) -> None:
        with TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "personal"
            session_file = session_path.with_suffix(".session")
            session_file.write_text("", encoding="utf-8")
            os.chmod(session_file, 0o644)

            secure_telethon_session_files(session_path)

            self.assertEqual(Path(tmp).stat().st_mode & 0o777, 0o700)
            self.assertEqual(session_file.stat().st_mode & 0o777, 0o600)

    def test_check_runtime_does_not_print_secret_values(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "TELEGRAM_API_ID": "123",
                    "TELEGRAM_API_HASH": "hash-secret",
                    "TELEGRAM_USER_PHONE_NUMBER": "+10000000000",
                    "TELEGRAM_USER_SESSION_DIR": tmp,
                },
                clear=True,
            ):
                config = AppConfig.from_env()

            class Output:
                value = ""

                def write(self, text: str) -> int:
                    self.value += text
                    return len(text)

                def flush(self) -> None:
                    return None

            output = Output()
            check_runtime(config, output)  # type: ignore[arg-type]

            self.assertNotIn("hash-secret", output.value)
            self.assertNotIn("+10000000000", output.value)
