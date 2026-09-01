from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol

from telegram_secretary.models import CalendarWindow


class CalendarProvider(Protocol):
    def free_windows(
        self,
        start: datetime,
        end: datetime,
        duration: timedelta,
    ) -> list[CalendarWindow]:
        ...


@dataclass(frozen=True)
class BusyWindow:
    start: datetime
    end: datetime


class StaticCalendarProvider:
    """Small deterministic calendar provider for local tests and dry-run demos."""

    def __init__(self, busy_windows: list[BusyWindow] | None = None) -> None:
        self._busy_windows = sorted(busy_windows or [], key=lambda window: window.start)

    def free_windows(
        self,
        start: datetime,
        end: datetime,
        duration: timedelta,
    ) -> list[CalendarWindow]:
        return free_windows_from_busy(self._busy_windows, start, end, duration)


def free_windows_from_busy(
    busy_windows: list[BusyWindow],
    start: datetime,
    end: datetime,
    duration: timedelta,
) -> list[CalendarWindow]:
    if end <= start:
        return []

    free: list[CalendarWindow] = []
    cursor = start

    for busy in sorted(busy_windows, key=lambda window: window.start):
        if busy.end <= cursor or busy.start >= end:
            continue

        free_end = min(busy.start, end)
        if free_end - cursor >= duration:
            free.append(CalendarWindow(start=cursor, end=free_end))

        cursor = max(cursor, busy.end)
        if cursor >= end:
            break

    if end - cursor >= duration:
        free.append(CalendarWindow(start=cursor, end=end))

    return free


def default_business_window(now: datetime) -> tuple[datetime, datetime]:
    next_day = now.date() + timedelta(days=1)
    start = datetime.combine(next_day, time(hour=9), tzinfo=now.tzinfo)
    end = datetime.combine(next_day, time(hour=18), tzinfo=now.tzinfo)
    return start, end
