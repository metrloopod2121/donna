from __future__ import annotations

import re

from telegram_secretary.models import Classification, Importance, IncomingMessage, Intent


CALENDAR_TERMS = (
    "свобод",
    "занят",
    "можешь встрет",
    "есть окно",
    "есть время",
    "когда удобно",
    "available",
    "free",
    "have time",
    "meet",
)

ACTION_TERMS = (
    "сделай",
    "перенеси",
    "отмени",
    "забронируй",
    "создай",
    "ответь",
    "send",
    "book",
    "schedule",
    "reschedule",
    "cancel",
)

URGENT_TERMS = (
    "срочно",
    "важно",
    "горит",
    "asap",
    "urgent",
    "important",
)


class RuleBasedClassifier:
    """Deterministic MVP classifier used before adding an LLM provider."""

    question_re = re.compile(r"(\?|когда|можешь|can you|could you|are you)", re.IGNORECASE)

    def classify(self, message: IncomingMessage) -> Classification:
        text = message.text.strip().lower()
        reasons: list[str] = []

        has_calendar = any(term in text for term in CALENDAR_TERMS)
        has_action = any(term in text for term in ACTION_TERMS)
        has_question = bool(self.question_re.search(text))
        is_urgent = any(term in text for term in URGENT_TERMS)

        if has_action:
            intent = Intent.ACTION_REQUEST
            confidence = 0.82
            reasons.append("message contains an action verb")
        elif has_calendar and has_question:
            intent = Intent.CALENDAR_AVAILABILITY
            confidence = 0.9
            reasons.append("message matches calendar availability question terms")
        elif has_question:
            intent = Intent.GENERAL_QUESTION
            confidence = 0.75
            reasons.append("message looks like a question")
        elif text:
            intent = Intent.FYI
            confidence = 0.65
            reasons.append("message has no clear request")
        else:
            intent = Intent.UNKNOWN
            confidence = 0.4
            reasons.append("empty or unsupported message")

        if is_urgent or intent in {Intent.ACTION_REQUEST, Intent.CALENDAR_AVAILABILITY}:
            importance = Importance.HIGH
        elif has_question:
            importance = Importance.MEDIUM
        else:
            importance = Importance.LOW

        return Classification(
            importance=importance,
            intent=intent,
            confidence=confidence,
            reasons=tuple(reasons),
        )

