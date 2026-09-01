from __future__ import annotations

import os
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram_secretary.availability import AvailabilityService, format_today_busy_summary
from telegram_secretary.calendar import BusyWindow
from telegram_secretary.config import AppConfig
from telegram_secretary.models import CalendarWindow


class AvailabilityServiceTest(TestCase):
    def test_reports_calendar_not_connected_without_icloud_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CALENDAR_PROVIDER": "icloud_caldav",
                "DEFAULT_TIMEZONE": "Europe/Moscow",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        reply = AvailabilityService(config).free_today_reply(
            datetime(2026, 8, 22, 10, tzinfo=ZoneInfo("Europe/Moscow"))
        )

        self.assertIn("Apple Calendar пока не подключен", reply)
        self.assertNotIn("secret-value", reply)

    def test_today_reply_works_after_business_hours(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CALENDAR_PROVIDER": "icloud_caldav",
                "APPLE_CALENDAR_USERNAME": "configured",
                "APPLE_CALENDAR_APP_PASSWORD": "configured",
                "DEFAULT_TIMEZONE": "Europe/Moscow",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        tz = ZoneInfo("Europe/Moscow")
        provider = FakeCalendarProvider(
            busy=[
                BusyWindow(
                    start=datetime(2026, 8, 22, 10, tzinfo=tz),
                    end=datetime(2026, 8, 22, 11, tzinfo=tz),
                )
            ],
        )

        reply = AvailabilityService(config, provider=provider).today_reply(
            datetime(2026, 8, 22, 23, tzinfo=tz)
        )

        self.assertIn("Занятые интервалы сегодня: 10:00-11:00", reply)
        self.assertNotIn("рабочий интервал уже закончился", reply)

    def test_free_reply_still_reports_finished_business_interval(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CALENDAR_PROVIDER": "icloud_caldav",
                "APPLE_CALENDAR_USERNAME": "configured",
                "APPLE_CALENDAR_APP_PASSWORD": "configured",
                "DEFAULT_TIMEZONE": "Europe/Moscow",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        tz = ZoneInfo("Europe/Moscow")

        reply = AvailabilityService(config, provider=FakeCalendarProvider()).free_today_reply(
            datetime(2026, 8, 22, 23, tzinfo=tz)
        )

        self.assertEqual(reply, "На сегодня рабочий интервал уже закончился.")

    def test_today_summary_formats_full_day_busy_interval(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        reply = format_today_busy_summary(
            [
                BusyWindow(
                    start=datetime(2026, 8, 22, 0, 0, tzinfo=tz),
                    end=datetime(2026, 8, 23, 0, 0, tzinfo=tz),
                )
            ]
        )

        self.assertIn("весь день", reply)
        self.assertNotIn("00:00-00:00", reply)


class FakeCalendarProvider:
    def __init__(self, busy: list[BusyWindow] | None = None) -> None:
        self.busy = busy or []

    def busy_windows(self, start: datetime, end: datetime) -> list[BusyWindow]:
        return self.busy

    def free_windows(
        self,
        start: datetime,
        end: datetime,
        duration: timedelta,
    ) -> list[CalendarWindow]:
        return [CalendarWindow(start=start, end=end)]
