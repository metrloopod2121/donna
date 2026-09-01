from __future__ import annotations

from collections.abc import Awaitable, Callable

from telegram_secretary.config import AppConfig
from telegram_secretary.models import IncomingMessage


IncomingHandler = Callable[[IncomingMessage], Awaitable[None]]


async def run_telegram_user_ingestor(config: AppConfig, handler: IncomingHandler) -> None:
    """Reserved entry point for future Telethon-based user-account ingest.

    The current MVP deliberately refuses to authorize or stream the user's
    personal account from the bot service. Manual Telethon login and read-only
    ingest are separate follow-up steps after server-only secrets are present.
    """

    del config, handler
    raise RuntimeError(
        "Telegram user-account ingest is prepared for Telethon but disabled. "
        "Do not enter Telegram login codes or 2FA passwords in chat."
    )
