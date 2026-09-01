from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import TestCase

from telegram_secretary.calendar import BusyWindow, StaticCalendarProvider


class StaticCalendarProviderTest(TestCase):
    def test_free_windows_exclude_busy_ranges(self) -> None:
        start = datetime(2026, 8, 23, 9, tzinfo=timezone.utc)
        end = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
        provider = StaticCalendarProvider(
            [
                BusyWindow(
                    start=datetime(2026, 8, 23, 10, tzinfo=timezone.utc),
                    end=datetime(2026, 8, 23, 11, tzinfo=timezone.utc),
                )
            ]
        )

        windows = provider.free_windows(start, end, duration=timedelta(minutes=30))

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].start.hour, 9)
        self.assertEqual(windows[0].end.hour, 10)
        self.assertEqual(windows[1].start.hour, 11)
        self.assertEqual(windows[1].end.hour, 12)

