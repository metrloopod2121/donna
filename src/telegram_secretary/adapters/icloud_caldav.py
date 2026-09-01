from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from telegram_secretary.calendar import BusyWindow, CalendarProvider, free_windows_from_busy
from telegram_secretary.models import CalendarWindow


DAV = "{DAV:}"
CALDAV = "{urn:ietf:params:xml:ns:caldav}"
HTTPTransport = Callable[[str, str, bytes, dict[str, str]], bytes]


class CalendarConfigurationError(RuntimeError):
    pass


class CalendarConnectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalDAVCalendar:
    href: str
    display_name: str


class ICloudCalDAVCalendarProvider(CalendarProvider):
    """Read-only iCloud CalDAV availability adapter."""

    def __init__(
        self,
        username: str,
        app_password: str,
        calendar_ids: frozenset[str],
        base_url: str = "https://caldav.icloud.com",
        default_timezone: str = "Europe/Moscow",
        transport: HTTPTransport | None = None,
    ) -> None:
        if not username or not app_password:
            raise CalendarConfigurationError("iCloud calendar credentials are not configured.")
        self.username = username
        self._app_password = app_password
        self.calendar_ids = calendar_ids
        self.base_url = base_url.rstrip("/") + "/"
        self.default_timezone = ZoneInfo(default_timezone)
        self._transport = transport or self._urlopen_transport
        self._calendars: list[CalDAVCalendar] | None = None

    def free_windows(
        self,
        start: datetime,
        end: datetime,
        duration: timedelta,
    ) -> list[CalendarWindow]:
        busy = self.busy_windows(start, end)
        return free_windows_from_busy(busy, start, end, duration)

    def busy_windows(self, start: datetime, end: datetime) -> list[BusyWindow]:
        busy: list[BusyWindow] = []
        for calendar in self._discover_calendars():
            payload = self._calendar_query(calendar.href, start, end)
            busy.extend(parse_ics_busy_windows(payload, self.default_timezone))
        return _merge_busy_windows([_clip_busy_window(window, start, end) for window in busy])

    def _discover_calendars(self) -> list[CalDAVCalendar]:
        if self._calendars is not None:
            return self._calendars

        principal = self._current_user_principal()
        home = self._calendar_home_set(principal)
        calendars = self._calendar_list(home)
        if self.calendar_ids:
            calendars = [
                calendar
                for calendar in calendars
                if calendar.display_name in self.calendar_ids
                or calendar.href.rstrip("/").rsplit("/", 1)[-1] in self.calendar_ids
            ]
        self._calendars = calendars
        return calendars

    def _current_user_principal(self) -> str:
        body = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop><d:current-user-principal /></d:prop>
</d:propfind>"""
        root = self._xml_request("PROPFIND", self.base_url, body, {"Depth": "0"})
        href = root.findtext(f".//{DAV}current-user-principal/{DAV}href")
        if not href:
            raise CalendarConnectionError("CalDAV principal discovery failed.")
        return urljoin(self.base_url, href)

    def _calendar_home_set(self, principal_url: str) -> str:
        body = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-home-set /></d:prop>
</d:propfind>"""
        root = self._xml_request("PROPFIND", principal_url, body, {"Depth": "0"})
        href = root.findtext(f".//{CALDAV}calendar-home-set/{DAV}href")
        if not href:
            raise CalendarConnectionError("CalDAV calendar home discovery failed.")
        return urljoin(self.base_url, href)

    def _calendar_list(self, home_url: str) -> list[CalDAVCalendar]:
        body = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname />
    <d:resourcetype />
  </d:prop>
</d:propfind>"""
        root = self._xml_request("PROPFIND", home_url, body, {"Depth": "1"})
        calendars: list[CalDAVCalendar] = []
        for response in root.findall(f".//{DAV}response"):
            prop = response.find(f".//{DAV}prop")
            if prop is None or prop.find(f".//{CALDAV}calendar") is None:
                continue
            href = response.findtext(f"{DAV}href")
            if not href:
                continue
            display_name = prop.findtext(f"{DAV}displayname") or href.rstrip("/").rsplit("/", 1)[-1]
            calendars.append(CalDAVCalendar(href=urljoin(self.base_url, href), display_name=display_name))

        if not calendars:
            raise CalendarConnectionError("No readable iCloud calendars were discovered.")
        return calendars

    def _calendar_query(self, calendar_url: str, start: datetime, end: datetime) -> str:
        start_utc = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end_utc = end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag />
    <c:calendar-data>
      <c:expand start="{start_utc}" end="{end_utc}" />
    </c:calendar-data>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start_utc}" end="{end_utc}" />
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>""".encode("utf-8")
        root = self._xml_request("REPORT", calendar_url, body, {"Depth": "1"})
        return "\n".join(
            element.text or ""
            for element in root.iter()
            if element.tag == f"{CALDAV}calendar-data"
        )

    def _xml_request(
        self,
        method: str,
        url: str,
        body: bytes,
        extra_headers: dict[str, str],
    ) -> ElementTree.Element:
        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/xml; charset=utf-8",
            **extra_headers,
        }
        try:
            payload = self._transport(method, url, body, headers)
            return ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise CalendarConnectionError("CalDAV returned invalid XML.") from exc
        except Exception as exc:
            if isinstance(exc, CalendarConnectionError):
                raise
            raise CalendarConnectionError("CalDAV request failed.") from exc

    def _auth_header(self) -> str:
        raw = f"{self.username}:{self._app_password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    @staticmethod
    def _urlopen_transport(method: str, url: str, body: bytes, headers: dict[str, str]) -> bytes:
        request = Request(url, data=body, headers=headers, method=method)
        with urlopen(request, timeout=30) as response:
            status = response.status
            if status < 200 or status >= 300:
                raise CalendarConnectionError(f"CalDAV HTTP status {status}")
            return response.read()


def parse_ics_busy_windows(ics: str, default_timezone: ZoneInfo) -> list[BusyWindow]:
    busy: list[BusyWindow] = []
    for event in _parse_events(ics):
        if event.get("STATUS", {}).get("value", "").upper() == "CANCELLED":
            continue
        if event.get("TRANSP", {}).get("value", "").upper() == "TRANSPARENT":
            continue

        dtstart = event.get("DTSTART")
        dtend = event.get("DTEND")
        if not dtstart or not dtend:
            continue

        start = _parse_ical_datetime(dtstart["value"], dtstart["params"], default_timezone)
        end = _parse_ical_datetime(dtend["value"], dtend["params"], default_timezone)
        if end > start:
            busy.append(BusyWindow(start=start, end=end))
    return busy


def _parse_events(ics: str) -> list[dict[str, dict[str, Any]]]:
    events: list[dict[str, dict[str, Any]]] = []
    current: dict[str, dict[str, Any]] | None = None
    for line in _unfold_ics_lines(ics):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        left, value = line.split(":", 1)
        name, params = _parse_property_name(left)
        current[name] = {"value": value, "params": params}
    return events


def _unfold_ics_lines(ics: str) -> list[str]:
    lines: list[str] = []
    for raw in ics.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _parse_property_name(left: str) -> tuple[str, dict[str, str]]:
    parts = left.split(";")
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.upper()] = value.strip('"')
    return parts[0].upper(), params


def _parse_ical_datetime(value: str, params: dict[str, str], default_timezone: ZoneInfo) -> datetime:
    if params.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and "T" not in value):
        parsed_date = date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
        return datetime.combine(parsed_date, time.min, tzinfo=default_timezone)

    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)

    fmt = "%Y%m%dT%H%M%S" if len(value) == 15 else "%Y%m%dT%H%M"
    tz = ZoneInfo(params["TZID"]) if params.get("TZID") else default_timezone
    return datetime.strptime(value, fmt).replace(tzinfo=tz)


def _clip_busy_window(window: BusyWindow, start: datetime, end: datetime) -> BusyWindow:
    return BusyWindow(start=max(window.start, start), end=min(window.end, end))


def _merge_busy_windows(windows: list[BusyWindow]) -> list[BusyWindow]:
    valid = sorted(
        (window for window in windows if window.end > window.start),
        key=lambda window: window.start,
    )
    if not valid:
        return []

    merged = [valid[0]]
    for window in valid[1:]:
        previous = merged[-1]
        if window.start <= previous.end:
            merged[-1] = BusyWindow(start=previous.start, end=max(previous.end, window.end))
        else:
            merged.append(window)
    return merged

