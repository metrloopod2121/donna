from __future__ import annotations

from datetime import datetime, timedelta

from telegram_secretary.calendar import CalendarProvider
from telegram_secretary.config import AppConfig
from telegram_secretary.models import CalendarWindow


class AppleCalendarProvider(CalendarProvider):
    """Read-only Apple Calendar adapter.

    Production implementation options:
    - EventKit via `pyobjc-framework-EventKit`, preferred for local macOS service.
    - AppleScript/JXA as a simpler fallback, still requiring macOS Calendar permission.

    The adapter must return only busy/free windows to the auto-reply layer.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def free_windows(
        self,
        start: datetime,
        end: datetime,
        duration: timedelta,
    ) -> list[CalendarWindow]:
        raise NotImplementedError(
            "Connect read-only Apple Calendar access through EventKit or AppleScript/JXA."
        )

