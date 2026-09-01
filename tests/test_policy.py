from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase

from telegram_secretary.models import Classification, Importance, IncomingMessage, Intent, ReplyAction
from telegram_secretary.policy import AutoReplyPolicy


class AutoReplyPolicyTest(TestCase):
    def message(self, text: str, sender_id: str = "42") -> IncomingMessage:
        return IncomingMessage(
            message_id="m1",
            chat_id="c1",
            sender_id=sender_id,
            sender_name="Sender",
            text=text,
            received_at=datetime.now(timezone.utc),
        )

    def classification(self, intent: Intent, confidence: float = 0.9) -> Classification:
        return Classification(
            importance=Importance.HIGH,
            intent=intent,
            confidence=confidence,
            reasons=("test",),
        )

    def test_safe_calendar_question_from_trusted_sender_can_auto_send(self) -> None:
        policy = AutoReplyPolicy(
            trusted_sender_ids=frozenset({"42"}),
            auto_reply_enabled=True,
        )

        decision = policy.decide(
            self.message("Ты свободен завтра днем?"),
            self.classification(Intent.CALENDAR_AVAILABILITY),
        )

        self.assertEqual(decision.action, ReplyAction.AUTO_SEND)
        self.assertFalse(decision.requires_confirmation)

    def test_untrusted_sender_requires_draft(self) -> None:
        policy = AutoReplyPolicy(
            trusted_sender_ids=frozenset({"7"}),
            auto_reply_enabled=True,
        )

        decision = policy.decide(
            self.message("Ты свободен завтра днем?", sender_id="42"),
            self.classification(Intent.CALENDAR_AVAILABILITY),
        )

        self.assertEqual(decision.action, ReplyAction.DRAFT_FOR_APPROVAL)
        self.assertTrue(decision.requires_confirmation)

    def test_action_request_is_never_auto_sent(self) -> None:
        policy = AutoReplyPolicy(
            trusted_sender_ids=frozenset({"42"}),
            auto_reply_enabled=True,
        )

        decision = policy.decide(
            self.message("Забронируй встречу завтра"),
            self.classification(Intent.ACTION_REQUEST),
        )

        self.assertEqual(decision.action, ReplyAction.DRAFT_FOR_APPROVAL)

    def test_calendar_question_with_booking_action_requires_draft(self) -> None:
        policy = AutoReplyPolicy(
            trusted_sender_ids=frozenset({"42"}),
            auto_reply_enabled=True,
        )

        decision = policy.decide(
            self.message("Ты свободен завтра? Если да, забронируй встречу."),
            self.classification(Intent.CALENDAR_AVAILABILITY),
        )

        self.assertEqual(decision.action, ReplyAction.DRAFT_FOR_APPROVAL)

