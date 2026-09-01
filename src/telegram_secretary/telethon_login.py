from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from telegram_secretary.config import AppConfig
from telegram_secretary.telegram_user_account import prepare_telegram_user_session_dir


@dataclass(frozen=True)
class LoginPreflight:
    missing_secret_names: tuple[str, ...]
    api_id_is_valid: bool
    ingest_enabled: bool
    telethon_available: bool

    @property
    def ok(self) -> bool:
        return (
            not self.missing_secret_names
            and self.api_id_is_valid
            and not self.ingest_enabled
            and self.telethon_available
        )


def run_telethon_login_command(args: argparse.Namespace, output: TextIO = sys.stdout) -> int:
    config = AppConfig.from_env()
    if args.check_runtime:
        return check_runtime(config, output)
    return asyncio.run(run_interactive_login(config, output))


def check_runtime(config: AppConfig, output: TextIO) -> int:
    preflight = login_preflight(config)
    _write_preflight(preflight, output)
    session_path = telethon_session_path(config)
    prepare_telegram_user_session_dir(session_path.parent)
    print("Telethon session directory: ready", file=output)
    return 0 if preflight.ok else 1


async def run_interactive_login(config: AppConfig, output: TextIO) -> int:
    preflight = login_preflight(config)
    _write_preflight(preflight, output)
    if not preflight.ok:
        print("Login refused until preflight is clean.", file=output)
        return 2

    try:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
    except ImportError:
        print("Telethon is not installed in this image.", file=output)
        return 2

    session_path = telethon_session_path(config)
    prepare_telegram_user_session_dir(session_path.parent)
    client = TelegramClient(str(session_path), int(config.telegram_api_id), config.telegram_api_hash)
    print("Starting Telethon login. Enter Telegram code/2FA only in this server console.", file=output)

    try:
        await client.connect()
        if await client.is_user_authorized():
            print("Telethon session is already authorized.", file=output)
            return 0

        sent = await client.send_code_request(config.telegram_user_phone_number)
        code = getpass.getpass("Telegram code (server console only): ").strip()
        try:
            await client.sign_in(
                phone=config.telegram_user_phone_number,
                code=code,
                phone_code_hash=sent.phone_code_hash,
            )
        except SessionPasswordNeededError:
            password = getpass.getpass("Telegram 2FA password (server console only): ")
            await client.sign_in(password=password)

        print("Telethon login completed. Session is stored in the mounted sessions volume.", file=output)
        return 0
    finally:
        await client.disconnect()
        secure_telethon_session_files(session_path)


def login_preflight(config: AppConfig) -> LoginPreflight:
    missing = []
    if not config.telegram_api_id:
        missing.append("TELEGRAM_API_ID")
    if not config.telegram_api_hash:
        missing.append("TELEGRAM_API_HASH")
    if not config.telegram_user_phone_number:
        missing.append("TELEGRAM_USER_PHONE_NUMBER")
    return LoginPreflight(
        missing_secret_names=tuple(missing),
        api_id_is_valid=config.telegram_api_id.isdigit(),
        ingest_enabled=config.telegram_user_ingest_enabled,
        telethon_available=_telethon_available(),
    )


def telethon_session_path(config: AppConfig) -> Path:
    return config.telegram_user_session_dir / "personal"


def secure_telethon_session_files(session_path: Path) -> None:
    prepare_telegram_user_session_dir(session_path.parent)
    for candidate in (
        session_path,
        session_path.with_suffix(".session"),
        session_path.with_suffix(".session-journal"),
    ):
        if candidate.exists() and candidate.is_file():
            os.chmod(candidate, 0o600)


def add_telethon_login_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Only validate Telethon/env/session dir; do not start Telegram authorization.",
    )


def _telethon_available() -> bool:
    try:
        import telethon  # noqa: F401
    except ImportError:
        return False
    return True


def _write_preflight(preflight: LoginPreflight, output: TextIO) -> None:
    if preflight.missing_secret_names:
        print(
            "Missing server-only settings: " + ", ".join(preflight.missing_secret_names),
            file=output,
        )
    else:
        print("Server-only Telegram user settings: present", file=output)
    print(f"TELEGRAM_API_ID format: {'valid' if preflight.api_id_is_valid else 'invalid'}", file=output)
    print(f"TELEGRAM_USER_INGEST_ENABLED: {'true' if preflight.ingest_enabled else 'false'}", file=output)
    print(f"Telethon runtime: {'present' if preflight.telethon_available else 'missing'}", file=output)
