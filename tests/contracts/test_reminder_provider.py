"""Behaviour every ReminderProvider must satisfy."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from personal_ai_assistant.domain.reminder import ReminderDraft
from personal_ai_assistant.domain.result import DataStatus
from personal_ai_assistant.domain.types import TimeRange
from personal_ai_assistant.providers.reminder import ReminderProvider
from tests.fakes.clock import FakeClock
from tests.fakes.reminder import FakeReminderProvider

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone(timedelta(hours=8)))
WEEK = TimeRange(start=T0, end=T0 + timedelta(days=7))

type Factory = Callable[[str | None], ReminderProvider]


def _fake(outage: str | None) -> ReminderProvider:
    return FakeReminderProvider(clock=FakeClock(T0), outage=outage)


PROVIDERS: list[Factory] = [_fake]


@pytest.fixture(params=PROVIDERS, ids=lambda f: f.__name__)
def factory(request: pytest.FixtureRequest) -> Factory:
    return request.param  # type: ignore[no-any-return]


def test_created_reminders_are_listed_by_due_time(factory: Factory) -> None:
    provider = factory(None)
    later = provider.create_reminder(ReminderDraft(text="Dentist", due_at=T0 + timedelta(days=2)))
    provider.create_reminder(ReminderDraft(text="Call mom", due_at=T0 + timedelta(hours=3)))
    provider.create_reminder(ReminderDraft(text="Next month", due_at=T0 + timedelta(days=30)))
    assert later.data is not None
    assert later.data.due_at == T0 + timedelta(days=2)
    listed = provider.list_reminders(WEEK)
    assert listed.status is DataStatus.OK
    assert listed.data is not None
    assert [r.text for r in listed.data] == ["Call mom", "Dentist"]


def test_empty_window_is_a_verified_empty_success(factory: Factory) -> None:
    result = factory(None).list_reminders(WEEK)
    assert result.status is DataStatus.OK
    assert result.data == ()


def test_outage_is_reported_not_disguised_as_empty(factory: Factory) -> None:
    provider = factory("storage offline")
    draft = ReminderDraft(text="x", due_at=T0 + timedelta(hours=1))
    for result in (provider.create_reminder(draft), provider.list_reminders(WEEK)):
        assert not result.succeeded
        assert result.data is None
