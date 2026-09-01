from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import TestCase
from zoneinfo import ZoneInfo

from telegram_secretary.adapters.icloud_caldav import (
    ICloudCalDAVCalendarProvider,
    parse_ics_busy_windows,
)


class ICloudCalDAVTest(TestCase):
    def test_parse_ics_busy_windows_skips_transparent_and_cancelled_events(self) -> None:
        ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260822T100000Z
DTEND:20260822T110000Z
SUMMARY:Busy
END:VEVENT
BEGIN:VEVENT
DTSTART:20260822T120000Z
DTEND:20260822T130000Z
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
DTSTART:20260822T140000Z
DTEND:20260822T150000Z
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""

        windows = parse_ics_busy_windows(ics, ZoneInfo("Europe/Moscow"))

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].start.hour, 10)
        self.assertEqual(windows[0].end.hour, 11)

    def test_provider_discovers_calendar_and_returns_free_windows(self) -> None:
        responses = {
            ("PROPFIND", "https://caldav.icloud.com/"): b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:propstat><d:prop>
      <d:current-user-principal><d:href>/principal/</d:href></d:current-user-principal>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>""",
            ("PROPFIND", "https://caldav.icloud.com/principal/"): b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:propstat><d:prop>
      <c:calendar-home-set><d:href>/calendars/</d:href></c:calendar-home-set>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>""",
            ("PROPFIND", "https://caldav.icloud.com/calendars/"): b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/calendars/work/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Work</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>""",
            ("REPORT", "https://caldav.icloud.com/calendars/work/"): b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response><d:propstat><d:prop><c:calendar-data>BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260822T100000Z
DTEND:20260822T110000Z
END:VEVENT
END:VCALENDAR</c:calendar-data></d:prop></d:propstat></d:response>
</d:multistatus>""",
        }

        def transport(method: str, url: str, body: bytes, headers: dict[str, str]) -> bytes:
            self.assertIn("Authorization", headers)
            return responses[(method, url)]

        provider = ICloudCalDAVCalendarProvider(
            username="user@example.com",
            app_password="app-password",
            calendar_ids=frozenset({"Work"}),
            transport=transport,
        )

        windows = provider.free_windows(
            datetime(2026, 8, 22, 9, tzinfo=timezone.utc),
            datetime(2026, 8, 22, 12, tzinfo=timezone.utc),
            timedelta(minutes=30),
        )

        self.assertEqual([(window.start.hour, window.end.hour) for window in windows], [(9, 10), (11, 12)])

