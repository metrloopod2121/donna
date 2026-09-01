from __future__ import annotations

from datetime import timedelta

from telegram_secretary.calendar import CalendarProvider, default_business_window
from telegram_secretary.classifier import RuleBasedClassifier
from telegram_secretary.models import IncomingMessage, ReplyDecision
from telegram_secretary.notes import NotesProvider, build_daily_context, render_daily_context
from telegram_secretary.policy import AutoReplyPolicy
from telegram_secretary.storage import SQLiteStore


class SecretaryCore:
    def __init__(
        self,
        classifier: RuleBasedClassifier,
        policy: AutoReplyPolicy,
        calendar: CalendarProvider,
        notes: NotesProvider | None = None,
        store: SQLiteStore | None = None,
    ) -> None:
        self.classifier = classifier
        self.policy = policy
        self.calendar = calendar
        self.notes = notes
        self.store = store

    def handle_incoming(self, message: IncomingMessage) -> ReplyDecision:
        classification = self.classifier.classify(message)
        start, end = default_business_window(message.received_at)
        windows = self.calendar.free_windows(start, end, duration=timedelta(minutes=30))
        decision = self.policy.decide(message, classification, windows)

        if self.store is not None:
            self.store.save_incoming(message)
            self.store.save_decision(message, classification, decision)

        return decision

    def today_briefing(self, message: IncomingMessage) -> str:
        if self.notes is None:
            return "Источник заметок и задач пока не подключен."
        context = build_daily_context(self.notes, message.received_at.date())
        return render_daily_context(context)
