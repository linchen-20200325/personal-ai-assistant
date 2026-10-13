"""Behaviour every DocumentProvider must satisfy."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

import pytest

from personal_ai_assistant.domain.document import DocumentMeta, DocumentSearch, ReadDocument
from personal_ai_assistant.domain.result import DataStatus
from personal_ai_assistant.providers.document import DocumentProvider
from tests.fakes.clock import FakeClock
from tests.fakes.document import FakeDocumentProvider

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone(timedelta(hours=8)))

type Factory = Callable[[Mapping[DocumentMeta, str | None], str | None], DocumentProvider]


def _fake(docs: Mapping[DocumentMeta, str | None], outage: str | None) -> DocumentProvider:
    return FakeDocumentProvider(docs, clock=FakeClock(T0), outage=outage)


PROVIDERS: list[Factory] = [_fake]


def _meta(doc_id: str, title: str, days_ago: int) -> DocumentMeta:
    return DocumentMeta(
        doc_id=doc_id,
        title=title,
        mime_type="text/plain",
        modified_at=T0 - timedelta(days=days_ago),
        web_url=None,
    )


TRIP = _meta("trip", "Tokyo trip plan", 1)
BUDGET = _meta("budget", "Budget 2026", 3)
SCAN = _meta("scan", "Scanned receipt", 2)
DOCS: dict[DocumentMeta, str | None] = {
    TRIP: "Day 1: Asakusa. " * 100,
    BUDGET: "Rent, food, travel",
    SCAN: None,
}


@pytest.fixture(params=PROVIDERS, ids=lambda f: f.__name__)
def factory(request: pytest.FixtureRequest) -> Factory:
    return request.param  # type: ignore[no-any-return]


def test_search_most_recent_first_and_bounded(factory: Factory) -> None:
    result = factory(DOCS, None).search_documents(DocumentSearch(limit=2))
    assert result.data is not None
    assert [d.doc_id for d in result.data.items] == ["trip", "scan"]
    assert result.data.has_more is True


def test_search_by_text(factory: Factory) -> None:
    result = factory(DOCS, None).search_documents(DocumentSearch(text="tokyo"))
    assert result.data is not None
    assert [d.doc_id for d in result.data.items] == ["trip"]
    assert result.data.has_more is False


def test_read_states_truncation(factory: Factory) -> None:
    provider = factory(DOCS, None)
    cut = provider.read_document(ReadDocument(doc_id="trip", max_chars=50))
    assert cut.data is not None
    assert len(cut.data.text) == 50
    assert cut.data.truncated is True
    whole = provider.read_document(ReadDocument(doc_id="budget"))
    assert whole.data is not None
    assert whole.data.text == "Rent, food, travel"
    assert whole.data.truncated is False


@pytest.mark.parametrize("doc_id", ["scan", "missing"])
def test_unreadable_or_missing_is_a_failure_not_empty_text(factory: Factory, doc_id: str) -> None:
    result = factory(DOCS, None).read_document(ReadDocument(doc_id=doc_id))
    assert result.status is DataStatus.UNAVAILABLE
    assert result.data is None


def test_outage_is_reported_not_disguised_as_empty(factory: Factory) -> None:
    provider = factory(DOCS, "upstream timeout")
    for result in (
        provider.search_documents(DocumentSearch()),
        provider.read_document(ReadDocument(doc_id="trip")),
    ):
        assert not result.succeeded
        assert result.data is None
