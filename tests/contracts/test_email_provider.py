"""Behaviour every EmailProvider must satisfy."""

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

import pytest

from personal_ai_assistant.domain.email import EmailDraft, EmailMessage, EmailSearch
from personal_ai_assistant.domain.result import DataStatus
from personal_ai_assistant.domain.types import TimeRange
from personal_ai_assistant.providers.email import EmailProvider
from tests.fakes.clock import FakeClock
from tests.fakes.email import FakeEmailProvider

TPE = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 14, 9, 0, tzinfo=TPE)

type Factory = Callable[[Iterable[EmailMessage], str | None], EmailProvider]


def _fake(messages: Iterable[EmailMessage], outage: str | None) -> EmailProvider:
    return FakeEmailProvider(messages, clock=FakeClock(T0), outage=outage)


PROVIDERS: list[Factory] = [_fake]


def _msg(message_id: str, hours_ago: float | None, subject: str = "synthetic") -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        thread_id=None,
        subject=subject,
        sender="sender@example.com",
        recipients=("me@example.com",),
        received_at=None if hours_ago is None else T0 - timedelta(hours=hours_ago),
        snippet=None,
        body_text=f"body of {message_id}",
        labels=None,
        is_unread=None,
    )


# Mostly-unknown fields on purpose: adapters must not fill them in.
MID = EmailMessage(
    message_id="mid",
    thread_id=None,
    subject=None,
    sender=None,
    recipients=None,
    received_at=T0 - timedelta(hours=5),
    snippet=None,
    body_text="body of mid",
    labels=None,
    is_unread=None,
)


@pytest.fixture(params=PROVIDERS, ids=lambda f: f.__name__)
def factory(request: pytest.FixtureRequest) -> Factory:
    return request.param  # type: ignore[no-any-return]


def test_search_is_newest_first_and_bounded(factory: Factory) -> None:
    provider = factory([_msg("old", 30), _msg("new", 1), MID], None)
    result = provider.search_messages(EmailSearch(limit=2))
    assert result.status is DataStatus.OK
    assert result.data is not None
    assert [m.message_id for m in result.data.items] == ["new", "mid"]
    assert result.data.has_more is True
    # Search results may omit bodies, but every other field must be reported
    # exactly as stored: unknown stays None, never "" / () / False.
    assert result.data.items[1] == MID.model_copy(update={"body_text": None})


def test_search_by_window_and_text(factory: Factory) -> None:
    today = TimeRange(start=T0 - timedelta(hours=9), end=T0 + timedelta(hours=15))
    provider = factory(
        [_msg("a", 1, "Invoice due"), _msg("b", 2, "Lunch"), _msg("c", 48, "Invoice old")], None
    )
    result = provider.search_messages(EmailSearch(text="invoice", window=today))
    assert result.data is not None
    assert [m.message_id for m in result.data.items] == ["a"]
    assert result.data.has_more is False


def test_no_match_is_a_verified_empty_page(factory: Factory) -> None:
    result = factory([_msg("a", 1)], None).search_messages(EmailSearch(text="nothing-matches"))
    assert result.status is DataStatus.OK
    assert result.data is not None
    assert result.data.items == ()
    assert result.data.has_more is False


def test_get_returns_full_message_or_failure(factory: Factory) -> None:
    provider = factory([_msg("a", 1)], None)
    found = provider.get_message("a")
    assert found.data == _msg("a", 1)
    missing = provider.get_message("zzz")
    assert not missing.succeeded
    assert missing.data is None


def test_draft_is_saved_and_echoed(factory: Factory) -> None:
    draft = EmailDraft(to=("someone@example.com",), subject="Hi", body="Draft body")
    saved = factory([], None).create_draft(draft)
    assert saved.status is DataStatus.OK
    assert saved.data is not None
    assert saved.data.draft == draft
    assert saved.provenance is not None
    assert saved.provenance.source_ref == saved.data.draft_id


def test_outage_is_reported_not_disguised_as_empty(factory: Factory) -> None:
    provider = factory([_msg("a", 1)], "upstream timeout")
    draft = EmailDraft(to=("x@example.com",), subject="s", body="b")
    for result in (
        provider.search_messages(EmailSearch()),
        provider.get_message("a"),
        provider.create_draft(draft),
    ):
        assert not result.succeeded
        assert result.data is None
        assert result.error
