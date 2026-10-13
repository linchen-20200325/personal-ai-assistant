"""Behaviour every CalendarProvider must satisfy.

Add each real adapter (e.g. a Google adapter fed recorded responses) to
``PROVIDERS`` so it is held to the same contract as the fake.
"""

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

import pytest

from personal_ai_assistant.domain.calendar import CalendarEvent, EventDraft
from personal_ai_assistant.domain.result import DataStatus
from personal_ai_assistant.domain.types import TimeRange
from personal_ai_assistant.providers.calendar import CalendarProvider
from tests.fakes.calendar import FakeCalendarProvider
from tests.fakes.clock import FakeClock

TPE = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 14, 9, 0, tzinfo=TPE)
DAY = TimeRange(start=T0.replace(hour=0), end=T0.replace(hour=0) + timedelta(days=1))

type Factory = Callable[[Iterable[CalendarEvent], str | None], CalendarProvider]


def _fake(events: Iterable[CalendarEvent], outage: str | None) -> CalendarProvider:
    return FakeCalendarProvider(events, clock=FakeClock(T0), outage=outage)


PROVIDERS: list[Factory] = [_fake]


def _event(event_id: str, start: datetime, hours: float | None) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        title=f"synthetic {event_id}",
        start=start,
        end=None if hours is None else start + timedelta(hours=hours),
        location=None,
        attendees=None,
    )


@pytest.fixture(params=PROVIDERS, ids=lambda f: f.__name__)
def factory(request: pytest.FixtureRequest) -> Factory:
    return request.param  # type: ignore[no-any-return]


def test_lists_overlapping_events_in_start_order(factory: Factory) -> None:
    events = [
        _event("late", T0 + timedelta(hours=5), 1),
        _event("early", T0, 1),
        _event("tomorrow", T0 + timedelta(days=1), 1),
        _event("ends-at-window-start", DAY.start - timedelta(hours=1), 1),
    ]
    result = factory(events, None).list_events(DAY)
    assert result.status is DataStatus.OK
    assert result.data is not None
    assert [e.event_id for e in result.data] == ["early", "late"]
    assert result.provenance is not None
    assert result.provenance.retrieved_at.tzinfo is not None


def test_no_events_is_a_verified_empty_success(factory: Factory) -> None:
    result = factory([], None).list_events(DAY)
    assert result.status is DataStatus.OK
    assert result.data == ()
    assert result.provenance is not None


def test_outage_is_reported_not_disguised_as_empty(factory: Factory) -> None:
    provider = factory([_event("e", T0, 1)], "upstream timeout")
    listed = provider.list_events(DAY)
    created = provider.create_event(EventDraft(title="x", start=T0, end=T0 + timedelta(hours=1)))
    for result in (listed, created):
        assert result.status in (DataStatus.ERROR, DataStatus.UNAVAILABLE)
        assert result.data is None
        assert result.error


def test_created_event_echoes_draft_and_is_listed(factory: Factory) -> None:
    provider = factory([], None)
    draft = EventDraft(title="Dentist", start=T0, end=T0 + timedelta(hours=1), location="Clinic")
    created = provider.create_event(draft)
    assert created.status is DataStatus.OK
    assert created.data is not None
    assert (created.data.title, created.data.start, created.data.end) == (
        draft.title,
        draft.start,
        draft.end,
    )
    assert created.provenance is not None
    assert created.provenance.source_ref == created.data.event_id
    listed = provider.list_events(DAY)
    assert listed.data is not None
    assert [e.event_id for e in listed.data] == [created.data.event_id]
