from __future__ import annotations

from telegram_secretary.config import AppConfig
from telegram_secretary.storage import SQLiteStore


async def run_secretary_bot(config: AppConfig, store: SQLiteStore) -> None:
    """Run a private aiogram bot for summaries and approval actions.

    The first production iteration should add inline buttons for approve/reject and map them to
    pending draft IDs in storage.
    """

    try:
        from aiogram import Bot, Dispatcher, types
        from aiogram.filters import Command
    except ImportError as exc:
        raise RuntimeError("Install the aiogram dependency to use the secretary bot.") from exc

    if not config.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN must be set locally.")

    bot = Bot(token=config.telegram_bot_token)
    dispatcher = Dispatcher()

    def is_owner(message: types.Message) -> bool:
        return str(message.from_user.id) == config.secretary_owner_telegram_id

    @dispatcher.message(Command("inbox"))
    async def inbox(message: types.Message) -> None:
        if not is_owner(message):
            return
        rows = store.recent_messages(limit=10)
        if not rows:
            await message.answer("Входящих пока нет.")
            return
        lines = [f"{row['received_at']} - {row['sender_name']}: {row['text']}" for row in rows]
        await message.answer("\n".join(lines))

    @dispatcher.message(Command("summary"))
    async def summary(message: types.Message) -> None:
        if not is_owner(message):
            return
        rows = store.recent_messages(limit=20)
        await message.answer(f"Зафиксировано входящих: {len(rows)}. Детальная сводка будет в LLM-итерации.")

    await dispatcher.start_polling(bot)

