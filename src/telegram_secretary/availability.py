from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram_secretary.adapters.icloud_caldav import (
    CalendarConfigurationError,
    CalendarConnectionError,
    ICloudCalDAVCalendarProvider,
)
from telegram_secretary.calendar import BusyWindow
from telegram_secretary.config import AppConfig
from telegram_secretary.models import CalendarWindow


class AvailabilityService:
    def __init__(self, config: AppConfig, provider: ICloudCalDAVCalendarProvider | None = None) -> None:
        self.config = config
        self._provider = provider

    def free_today_reply(self, now: datetime | None = None) -> str:
        tz = ZoneInfo(self.config.default_timezone)
        current = (now or datetime.now(tz)).astimezone(tz)
        start, end = _today_window(current)
        if end <= start:
            return "На сегодня рабочий интервал уже закончился."

        try:
            windows = self._calendar_provider().free_windows(
                start,
                end,
                duration=timedelta(minutes=30),
            )
        except CalendarConfigurationError:
            return _calendar_not_connected_text()
        except CalendarConnectionError:
            return (
                "Не удалось прочитать Apple Calendar. Проверь iCloud username, app-specific "
                "password и список календарей на сервере."
            )

        return format_free_windows_today(windows)

    def today_reply(self, now: datetime | None = None) -> str:
        tz = ZoneInfo(self.config.default_timezone)
        current = (now or datetime.now(tz)).astimezone(tz)
        start, end = _full_today_window(current)

        try:
            busy_windows = self._calendar_provider().busy_windows(start, end)
        except CalendarConfigurationError:
            return _calendar_not_connected_text()
        except CalendarConnectionError:
            return (
                "Не удалось прочитать Apple Calendar. Проверь iCloud username, app-specific "
                "password и список календарей на сервере."
            )

        return format_today_busy_summary(busy_windows)

    def _calendar_provider(self) -> ICloudCalDAVCalendarProvider:
        if self.config.calendar_provider not in {"icloud_caldav", "apple_caldav"}:
            raise CalendarConfigurationError("Calendar provider is not iCloud CalDAV.")

        if self._provider is not None:
            return self._provider

        if not self.config.apple_calendar_username or not self.config.apple_calendar_app_password:
            raise CalendarConfigurationError("iCloud calendar credentials are not configured.")

        return ICloudCalDAVCalendarProvider(
            username=self.config.apple_calendar_username,
            app_password=self.config.apple_calendar_app_password,
            calendar_ids=self.config.apple_calendar_ids,
            base_url=self.config.apple_calendar_caldav_url,
            default_timezone=self.config.default_timezone,
        )


def format_free_windows_today(windows: list[CalendarWindow]) -> str:
    if not windows:
        return "Сегодня не вижу свободных окон длиной от 30 минут."
    labels = ", ".join(f"{window.start:%H:%M}-{window.end:%H:%M}" for window in windows[:8])
    suffix = "" if len(windows) <= 8 else " и еще несколько окон"
    return f"Свободные окна сегодня: {labels}{suffix}."


def format_today_busy_summary(windows: list[BusyWindow]) -> str:
    if not windows:
        return "Сегодня в календаре нет занятых интервалов."
    labels = ", ".join(_busy_window_label(window) for window in windows[:12])
    suffix = "" if len(windows) <= 12 else " и еще несколько интервалов"
    return f"Занятые интервалы сегодня: {labels}{suffix}."


def _busy_window_label(window: BusyWindow) -> str:
    if (
        window.start.time() == time.min
        and window.end.time() == time.min
        and window.end.date() > window.start.date()
    ):
        return "весь день"
    return f"{window.start:%H:%M}-{window.end:%H:%M}"


def _today_window(now: datetime) -> tuple[datetime, datetime]:
    day_start = datetime.combine(now.date(), time(hour=9), tzinfo=now.tzinfo)
    day_end = datetime.combine(now.date(), time(hour=18), tzinfo=now.tzinfo)
    start = max(day_start, _ceil_to_next_quarter(now))
    return start, day_end


def _full_today_window(now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    return start, start + timedelta(days=1)


def _ceil_to_next_quarter(value: datetime) -> datetime:
    minute = ((value.minute + 14) // 15) * 15
    if minute == 60:
        return value.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return value.replace(minute=minute, second=0, microsecond=0)


def _calendar_not_connected_text() -> str:
    return (
        "Apple Calendar пока не подключен. Добавь iCloud username и app-specific password "
        "в server secrets.env, не отправляя их в чат."
    )
