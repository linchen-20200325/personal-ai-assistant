"""In-process fake of the Calendar v3 ``events.list`` endpoint (synthetic data only).

Plugs into the real ``googleapiclient`` client as its ``http`` object, so the
adapter is exercised end to end (request building, paging, JSON decoding,
HttpError mapping) without any network access or Google account.
"""

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httplib2
from googleapiclient.discovery import build

from personal_ai_assistant.adapters.google_calendar import GoogleCalendarProvider
from personal_ai_assistant.clock import Clock
from personal_ai_assistant.domain.calendar import CalendarEvent

type Outage = tuple[int, Mapping[str, Any]] | BaseException


def to_google(event: CalendarEvent) -> dict[str, Any]:
    """Render a domain event the way Google would send it."""
    raw: dict[str, Any] = {
        "id": event.event_id,
        "status": "confirmed",
        "start": {"dateTime": event.start.isoformat()},
    }
    if event.end is not None:
        raw["end"] = {"dateTime": event.end.isoformat()}
    if event.title is not None:
        raw["summary"] = event.title
    if event.location is not None:
        raw["location"] = event.location
    if event.attendees is not None:
        raw["attendees"] = [{"email": a, "responseStatus": "needsAction"} for a in event.attendees]
    return raw


def _start_of(raw: Mapping[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(raw["start"]["dateTime"])
    except (KeyError, TypeError, ValueError):
        return None


def _end_of(raw: Mapping[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(raw["end"]["dateTime"])
    except (KeyError, TypeError, ValueError):
        return None


def _sort_key(raw: Mapping[str, Any]) -> float:
    start = _start_of(raw)
    return start.timestamp() if start is not None else float("inf")


class FakeCalendarHttp:
    """Minimal ``httplib2.Http`` stand-in serving one calendar."""

    def __init__(
        self,
        items: Iterable[Mapping[str, Any]] = (),
        *,
        time_zone: str | None = "Asia/Taipei",
        page_size: int | None = None,
        outage: Outage | None = None,
        body: Any = None,
        calendar_id: str = "primary",
    ) -> None:
        self.items = list(items)
        self.time_zone = time_zone
        self.page_size = page_size
        self.outage = outage
        self.body = body  # raw override of the response body
        self.calendar_id = calendar_id
        self.requests: list[dict[str, Any]] = []

    def request(
        self, uri: str, method: str = "GET", body: Any = None, headers: Any = None, **_: Any
    ) -> tuple[httplib2.Response, bytes]:
        parsed = urlparse(uri)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.requests.append({"method": method, "path": unquote(parsed.path), "query": query})
        if isinstance(self.outage, BaseException):
            raise self.outage
        if self.outage is not None:
            status, payload = self.outage
            return self._respond(status, payload)
        if self.body is not None:
            return self._respond(200, self.body)
        if unquote(parsed.path) != f"/calendar/v3/calendars/{self.calendar_id}/events":
            return self._respond(404, {"error": {"code": 404, "errors": [{"reason": "notFound"}]}})
        hits = [i for i in self.items if self._overlaps(i, query)]
        hits.sort(key=_sort_key)
        size = self.page_size or int(query.get("maxResults", "250"))
        offset = int(query.get("pageToken", "0"))
        page: dict[str, Any] = {"kind": "calendar#events", "items": hits[offset : offset + size]}
        if self.time_zone is not None:
            page["timeZone"] = self.time_zone
        if offset + size < len(hits):
            page["nextPageToken"] = str(offset + size)
        return self._respond(200, page)

    @staticmethod
    def _overlaps(raw: Mapping[str, Any], query: Mapping[str, str]) -> bool:
        start, end = _start_of(raw), _end_of(raw)
        if start is None:
            return True  # malformed items are passed through for the adapter to judge
        time_min = datetime.fromisoformat(query["timeMin"])
        time_max = datetime.fromisoformat(query["timeMax"])
        if end is None:
            return time_min <= start < time_max
        return start < time_max and end > time_min

    @staticmethod
    def _respond(status: int, payload: Any) -> tuple[httplib2.Response, bytes]:
        response = httplib2.Response({"status": str(status), "content-type": "application/json"})
        return response, json.dumps(payload).encode()


def service_factory(http: FakeCalendarHttp) -> Callable[[], Any]:
    def factory() -> Any:
        return build("calendar", "v3", http=http, cache_discovery=False, static_discovery=True)

    return factory


def google_provider(http: FakeCalendarHttp, *, clock: Clock) -> GoogleCalendarProvider:
    return GoogleCalendarProvider(
        service_factory(http), clock=clock, calendar_id=http.calendar_id, num_retries=0
    )
