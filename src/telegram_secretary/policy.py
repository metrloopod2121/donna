from __future__ import annotations

import re
from dataclasses import dataclass

from telegram_secretary.models import (
    CalendarWindow,
    Classification,
    IncomingMessage,
    Intent,
    ReplyAction,
    ReplyDecision,
)


SAFE_AVAILABILITY_RE = re.compile(
    r"("
    r"свобод|есть\s+время|есть\s+окно|когда\s+удобно|можешь\s+встрет"
    r"|available|are\s+you\s+free|do\s+you\s+have\s+time|when\s+can\s+you"
    r")",
    re.IGNORECASE,
)

UNSAFE_ACTION_RE = re.compile(
    r"("
    r"забронируй|создай|перенеси|отмени|подтверди|пообещай|ответь\s+ему"
    r"|book|schedule|reschedule|cancel|confirm|promise|reply\s+to"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AutoReplyPolicy:
    trusted_sender_ids: frozenset[str]
    auto_reply_enabled: bool = False
    min_confidence: float = 0.85

    def decide(
        self,
        message: IncomingMessage,
        classification: Classification,
        calendar_windows: list[CalendarWindow] | None = None,
    ) -> ReplyDecision:
        if classification.intent != Intent.CALENDAR_AVAILABILITY:
            return self._draft(
                message,
                classification,
                "non-calendar messages require user confirmation",
            )

        if not self.auto_reply_enabled:
            return self._draft(message, classification, "auto-reply is disabled")

        if message.sender_id not in self.trusted_sender_ids:
            return self._draft(message, classification, "sender is not in auto-reply allowlist")

        if classification.confidence < self.min_confidence:
            return self._draft(message, classification, "classification confidence is too low")

        if not is_safe_calendar_question(message.text):
            return self._draft(message, classification, "message is not a safe availability question")

        draft = availability_reply(calendar_windows or [])
        return ReplyDecision(
            action=ReplyAction.AUTO_SEND,
            draft_text=draft,
            reasons=classification.reasons + ("safe calendar auto-reply policy passed",),
            requires_confirmation=False,
        )

    def _draft(
        self,
        message: IncomingMessage,
        classification: Classification,
        reason: str,
    ) -> ReplyDecision:
        return ReplyDecision(
            action=ReplyAction.DRAFT_FOR_APPROVAL,
            draft_text=draft_reply(message, classification),
            reasons=classification.reasons + (reason,),
            requires_confirmation=True,
        )


def is_safe_calendar_question(text: str) -> bool:
    return bool(SAFE_AVAILABILITY_RE.search(text)) and not bool(UNSAFE_ACTION_RE.search(text))


def availability_reply(windows: list[CalendarWindow]) -> str:
    if not windows:
        return "Сейчас не вижу свободного окна в проверенном диапазоне. Вернусь с вариантами позже."

    labels = ", ".join(window.label() for window in windows[:3])
    return f"Да, есть свободные окна: {labels}. Могу уточнить, какое подойдет."


def draft_reply(message: IncomingMessage, classification: Classification) -> str:
    if classification.intent == Intent.CALENDAR_AVAILABILITY:
        return "Похоже, спрашивают про доступность. Проверь календарь и подтверди ответ."
    if classification.intent == Intent.ACTION_REQUEST:
        return "Нужно действие или решение. Подтверди, что именно ответить."
    if message.text.strip():
        return "Нужен ответ пользователя. Подготовь черновик перед отправкой."
    return "Сообщение пустое или неподдерживаемое. Автоответ не отправлен."

