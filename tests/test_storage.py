from __future__ import annotations

from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase

from telegram_secretary.models import (
    Classification,
    Importance,
    IncomingMessage,
    Intent,
    ReplyAction,
    ReplyDecision,
)
from telegram_secretary.storage import SQLiteStore


class SQLiteStoreTest(TestCase):
    def test_saves_and_lists_recent_messages(self) -> None:
        with TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "secretary.sqlite3")
            store.initialize()
            message = IncomingMessage(
                message_id="m1",
                chat_id="c1",
                sender_id="42",
                sender_name="Sender",
                text="Привет",
                received_at=datetime.now(timezone.utc),
            )
            classification = Classification(
                importance=Importance.LOW,
                intent=Intent.FYI,
                confidence=0.8,
            )
            decision = ReplyDecision(
                action=ReplyAction.DRAFT_FOR_APPROVAL,
                draft_text="draft",
                reasons=("test",),
                requires_confirmation=True,
            )

            store.save_incoming(message)
            store.save_decision(message, classification, decision)

            rows = store.recent_messages()

            self.assertEqual(rows[0]["message_id"], "m1")
            self.assertEqual(rows[0]["sender_name"], "Sender")

