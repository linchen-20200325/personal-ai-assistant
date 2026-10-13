"""In-memory calendar provider with synthetic data only."""

from collections.abc import Iterable

from personal_ai_assistant.clock import Clock
from personal_ai_assistant.domain.calendar import CalendarEvent, EventDraft
from personal_ai_assistant.domain.result import Provenance, ToolResult
from personal_ai_assistant.domain.types import TimeRange

SOURCE = "fake_calendar"


def _overlaps(event: CalendarEvent, window: TimeRange) -> bool:
    if event.end is None:  # unknown duration: treat as a point in time
        return window.start <= event.start < window.end
    return event.start < window.end and event.end > window.start


class FakeCalendarProvider:
    def __init__(
        self, events: Iterable[CalendarEvent] = (), *, clock: Clock, outage: str | None = None
    ) -> None:
        self.events = list(events)
        self.outage = outage
        self.created: list[CalendarEvent] = []
        self._clock = clock

    def list_events(self, window: TimeRange) -> ToolResult[tuple[CalendarEvent, ...]]:
        if self.outage:
            return ToolResult.failed(self.outage)
        hits = tuple(
            sorted((e for e in self.events if _overlaps(e, window)), key=lambda e: e.start)
        )
        return ToolResult.ok(
            hits, Provenance(source=SOURCE, retrieved_at=self._clock(), source_ref=None)
        )

    def create_event(self, draft: EventDraft) -> ToolResult[CalendarEvent]:
        if self.outage:
            return ToolResult.failed(self.outage)
        event = CalendarEvent(
            event_id=f"fake-{len(self.events) + 1}",
            title=draft.title,
            start=draft.start,
            end=draft.end,
            location=draft.location,
            attendees=draft.attendees,
        )
        self.events.append(event)
        self.created.append(event)
        return ToolResult.ok(
            event, Provenance(source=SOURCE, retrieved_at=self._clock(), source_ref=event.event_id)
        )
