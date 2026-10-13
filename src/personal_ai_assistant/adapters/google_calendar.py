"""Google Calendar read-only ``CalendarProvider``.

Data access only: fetch, normalise, validate, attach provenance. Nothing here
interprets or summarises events, and nothing is invented:

* A field Google does not send stays ``None`` (unknown). A field Google sends
  in a form we cannot represent faithfully is set to ``None`` and listed in
  ``missing`` (status PARTIAL); an event without a usable id or start is
  dropped and listed the same way.
* All-day dates are anchored at midnight in the calendar's own time zone as
  reported by Google; if Google reports none, the event is not guessed.
* Cancelled instances are skipped (they are not on the calendar).
* Paging stops after ``MAX_PAGES``; a longer listing is PARTIAL, never
  presented as complete.
* Failures map to ERROR / UNAVAILABLE with fixed, credential-free messages.

``create_event`` always refuses: this adapter has read-only scope by design.
"""

import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from pydantic import TypeAdapter, ValidationError

from personal_ai_assistant.adapters.google_auth import LOGIN_HINT, GoogleAuthUnavailable
from personal_ai_assistant.clock import Clock
from personal_ai_assistant.domain.calendar import CalendarEvent, EventDraft
from personal_ai_assistant.domain.result import DataStatus, Provenance, ToolResult
from personal_ai_assistant.domain.types import EmailAddress, TimeRange

SOURCE = "google_calendar"
PAGE_SIZE = 250
MAX_PAGES = 10
TRUNCATED_MARKER = "events_beyond_page_limit"
_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})
_EMAIL = TypeAdapter(EmailAddress)
_LABEL_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

type Events = tuple[CalendarEvent, ...]


class _Failure(Exception):
    def __init__(self, reason: str, status: DataStatus) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _zone(name: object) -> ZoneInfo | None:
    if not isinstance(name, str) or not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def parse_time(value: object, calendar_zone: ZoneInfo | None) -> datetime | None:
    """Google ``EventDateTime`` -> aware datetime, or None when not representable."""
    if not isinstance(value, Mapping):
        return None
    stamp = value.get("dateTime")
    if stamp is not None:
        if not isinstance(stamp, str):
            return None
        try:
            parsed = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        offset = parsed.utcoffset()
        if offset is None or offset.total_seconds() % 60:
            return None  # naive or sub-minute offset: not digest-stable, not guessed
        return parsed
    day = value.get("date")
    if isinstance(day, str):
        zone = _zone(value.get("timeZone")) or calendar_zone
        if zone is None:
            return None
        try:
            return datetime.combine(date.fromisoformat(day), time(0, 0), tzinfo=zone)
        except ValueError:
            return None
    return None


def _optional_text(raw: Mapping[str, Any], key: str, gaps: list[str], label: str) -> str | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if isinstance(value, str):
        return value
    gaps.append(f"{label}.{key}")
    return None


def _attendees(raw: Mapping[str, Any], gaps: list[str], label: str) -> tuple[str, ...] | None:
    if raw.get("attendeesOmitted") is True or "attendees" not in raw:
        return None  # Google omits guests it does not show; unknown, not "none"
    value = raw["attendees"]
    if not isinstance(value, list):
        gaps.append(f"{label}.attendees")
        return None
    emails: list[str] = []
    for entry in value:
        email = entry.get("email") if isinstance(entry, Mapping) else None
        try:
            emails.append(_EMAIL.validate_python(email))
        except ValidationError:
            # One unrepresentable guest makes the list incomplete: report, do not trim.
            gaps.append(f"{label}.attendees")
            return None
    return tuple(emails)


def map_event(
    raw: object, index: int, calendar_zone: ZoneInfo | None
) -> tuple[CalendarEvent | None, list[str]]:
    """One Google event -> (event or None if dropped, gaps)."""
    label = f"event[#{index}]"
    if not isinstance(raw, Mapping):
        return None, [label]
    if raw.get("status") == "cancelled":
        return None, []
    event_id = raw.get("id")
    if not isinstance(event_id, str) or not event_id:
        return None, [f"{label}.id"]
    if _LABEL_ID.fullmatch(event_id):
        label = f"event[{event_id}]"  # Google ids are opaque ASCII; more useful than a position
    start = parse_time(raw.get("start"), calendar_zone)
    if start is None:
        return None, [f"{label}.start"]
    gaps: list[str] = []
    end: datetime | None = None
    if raw.get("end") is not None:
        end = parse_time(raw.get("end"), calendar_zone)
        if end is None or end <= start:
            gaps.append(f"{label}.end")
            end = None
    try:
        event = CalendarEvent(
            event_id=event_id,
            title=_optional_text(raw, "summary", gaps, label),
            start=start,
            end=end,
            location=_optional_text(raw, "location", gaps, label),
            attendees=_attendees(raw, gaps, label),
        )
    except ValidationError:
        return None, [label]
    return event, gaps


def _overlaps(event: CalendarEvent, window: TimeRange) -> bool:
    if event.end is None:
        return window.start <= event.start < window.end
    return event.start < window.end and event.end > window.start


def _http_failure(exc: HttpError) -> _Failure:
    status = exc.resp.status if exc.resp is not None else 0
    reasons: set[str] = set()
    try:
        body = json.loads(exc.content)
        reasons = {
            str(e.get("reason")) for e in body["error"].get("errors", []) if isinstance(e, dict)
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    if status == 429 or (status == 403 and reasons & _RATE_LIMIT_REASONS):
        return _Failure("Google Calendar rate limit reached; try again later", DataStatus.ERROR)
    if status == 401:
        return _Failure(f"Google authorization was rejected; {LOGIN_HINT}", DataStatus.UNAVAILABLE)
    if status == 403:
        return _Failure("no permission to read this calendar", DataStatus.UNAVAILABLE)
    if status == 404:
        return _Failure("calendar not found", DataStatus.UNAVAILABLE)
    return _Failure(f"Google Calendar error (HTTP {status})", DataStatus.ERROR)


class GoogleCalendarProvider:
    """Read-only ``CalendarProvider`` over the Calendar v3 ``events.list`` API."""

    def __init__(
        self,
        service_factory: Callable[[], Any],
        *,
        clock: Clock,
        calendar_id: str = "primary",
        num_retries: int = 2,
    ) -> None:
        self._service_factory = service_factory
        self._service: Any = None
        self._clock = clock
        self._calendar_id = calendar_id
        self._num_retries = num_retries

    def list_events(self, window: TimeRange) -> ToolResult[Events]:
        try:
            items, calendar_zone, truncated = self._fetch_all(window)
        except _Failure as failure:
            return ToolResult.failed(failure.reason, status=failure.status)
        events: list[CalendarEvent] = []
        gaps: list[str] = []
        seen: set[str] = set()
        for index, raw in enumerate(items):
            event, event_gaps = map_event(raw, index, calendar_zone)
            gaps.extend(event_gaps)
            if event is not None and event.event_id not in seen and _overlaps(event, window):
                seen.add(event.event_id)
                events.append(event)
        if truncated:
            gaps.append(TRUNCATED_MARKER)
        data = tuple(sorted(events, key=lambda e: e.start))
        provenance = Provenance(
            source=SOURCE, retrieved_at=self._clock(), source_ref=self._calendar_id
        )
        if gaps:
            return ToolResult.partial(data, provenance, tuple(dict.fromkeys(gaps)))
        return ToolResult.ok(data, provenance)

    def create_event(self, draft: EventDraft) -> ToolResult[CalendarEvent]:
        return ToolResult.failed(
            "Google Calendar is connected read-only; creating events is not supported",
            status=DataStatus.UNAVAILABLE,
        )

    def _events_api(self) -> Any:
        if self._service is None:
            try:
                self._service = self._service_factory()
            except GoogleAuthUnavailable as exc:
                raise _Failure(str(exc), DataStatus.UNAVAILABLE) from None
            except Exception:
                raise _Failure(
                    "Google Calendar client could not be created", DataStatus.UNAVAILABLE
                ) from None
        return self._service.events()

    def _fetch_all(self, window: TimeRange) -> tuple[list[object], ZoneInfo | None, bool]:
        events_api = self._events_api()
        items: list[object] = []
        calendar_zone: ZoneInfo | None = None
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            body = self._fetch_page(events_api, window, page_token)
            page_items = body.get("items")
            if not isinstance(page_items, list):
                raise _Failure("unexpected Google Calendar response", DataStatus.ERROR)
            items.extend(page_items)
            calendar_zone = calendar_zone or _zone(body.get("timeZone"))
            next_token = body.get("nextPageToken")
            if next_token is None:
                return items, calendar_zone, False
            if not isinstance(next_token, str) or not next_token:
                raise _Failure("unexpected Google Calendar response", DataStatus.ERROR)
            page_token = next_token
        return items, calendar_zone, True

    def _fetch_page(
        self, events_api: Any, window: TimeRange, page_token: str | None
    ) -> Mapping[str, Any]:
        request = events_api.list(
            calendarId=self._calendar_id,
            timeMin=window.start.isoformat(),
            timeMax=window.end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            showDeleted=False,
            maxResults=PAGE_SIZE,
            pageToken=page_token,
        )
        try:
            body = request.execute(num_retries=self._num_retries)
        except HttpError as exc:
            raise _http_failure(exc) from None
        except RefreshError:
            raise _Failure(
                f"Google authorization expired or was revoked; {LOGIN_HINT}",
                DataStatus.UNAVAILABLE,
            ) from None
        except (TransportError, httplib2.HttpLib2Error, OSError):
            raise _Failure("Google Calendar is unreachable", DataStatus.ERROR) from None
        if not isinstance(body, Mapping):
            raise _Failure("unexpected Google Calendar response", DataStatus.ERROR)
        return body
