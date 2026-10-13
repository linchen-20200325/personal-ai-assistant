"""Behaviour every NoteProvider must satisfy."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from personal_ai_assistant.domain.note import NoteDraft, NoteSearch
from personal_ai_assistant.domain.result import DataStatus
from personal_ai_assistant.providers.note import NoteProvider
from tests.fakes.clock import FakeClock
from tests.fakes.note import FakeNoteProvider

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone(timedelta(hours=8)))

type Factory = Callable[[str | None], NoteProvider]


def _fake(outage: str | None) -> NoteProvider:
    return FakeNoteProvider(clock=FakeClock(T0), outage=outage)


PROVIDERS: list[Factory] = [_fake]


@pytest.fixture(params=PROVIDERS, ids=lambda f: f.__name__)
def factory(request: pytest.FixtureRequest) -> Factory:
    return request.param  # type: ignore[no-any-return]


def test_created_note_is_searchable_newest_first(factory: Factory) -> None:
    provider = factory(None)
    first = provider.create_note(NoteDraft(body="Buy train tickets"))
    second = provider.create_note(NoteDraft(title="Dentist", body="Bring insurance card"))
    assert first.status is DataStatus.OK
    assert second.data is not None
    assert second.data.title == "Dentist"
    found = provider.search_notes(NoteSearch())
    assert found.data is not None
    assert [n.body for n in found.data.items] == ["Bring insurance card", "Buy train tickets"]
    assert found.data.has_more is False


def test_search_by_text_and_limit(factory: Factory) -> None:
    provider = factory(None)
    for i in range(3):
        provider.create_note(NoteDraft(body=f"trip idea {i}"))
    provider.create_note(NoteDraft(body="unrelated"))
    page = provider.search_notes(NoteSearch(text="TRIP", limit=2))
    assert page.data is not None
    assert len(page.data.items) == 2
    assert page.data.has_more is True


def test_untitled_note_keeps_title_unknown(factory: Factory) -> None:
    provider = factory(None)
    created = provider.create_note(NoteDraft(body="x"))
    assert created.data is not None
    assert created.data.title is None
    found = provider.search_notes(NoteSearch())
    assert found.data is not None
    assert found.data.items == (created.data,)


def test_outage_is_reported_not_disguised_as_empty(factory: Factory) -> None:
    provider = factory("storage offline")
    for result in (provider.create_note(NoteDraft(body="x")), provider.search_notes(NoteSearch())):
        assert not result.succeeded
        assert result.data is None
