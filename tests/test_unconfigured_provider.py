"""The placeholder provider reports UNAVAILABLE for everything; never empty success."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from personal_ai_assistant.domain.calendar import EventDraft
from personal_ai_assistant.domain.document import DocumentSearch, ReadDocument
from personal_ai_assistant.domain.email import EmailDraft, EmailSearch
from personal_ai_assistant.domain.note import NoteDraft, NoteSearch
from personal_ai_assistant.domain.reminder import ReminderDraft
from personal_ai_assistant.domain.result import DataStatus, ToolResult
from personal_ai_assistant.domain.types import TimeRange
from personal_ai_assistant.providers.calendar import CalendarProvider
from personal_ai_assistant.providers.document import DocumentProvider
from personal_ai_assistant.providers.email import EmailProvider
from personal_ai_assistant.providers.note import NoteProvider
from personal_ai_assistant.providers.reminder import ReminderProvider
from personal_ai_assistant.providers.unconfigured import UnconfiguredProvider

P = UnconfiguredProvider()
# Structural conformance to every provider protocol (checked by mypy).
_PROTOCOLS: tuple[
    CalendarProvider, EmailProvider, DocumentProvider, NoteProvider, ReminderProvider
] = (P, P, P, P, P)

T = datetime(2026, 10, 14, 1, 0, tzinfo=UTC)
WINDOW = TimeRange(start=T, end=T.replace(hour=2))

CALLS: list[Callable[[], ToolResult[Any]]] = [
    lambda: P.list_events(WINDOW),
    lambda: P.create_event(EventDraft(title="t", start=T, end=T.replace(hour=2))),
    lambda: P.search_messages(EmailSearch(text="x")),
    lambda: P.get_message("m1"),
    lambda: P.create_draft(EmailDraft(to=("a@example.com",), subject="s", body="b")),
    lambda: P.search_documents(DocumentSearch(text="x")),
    lambda: P.read_document(ReadDocument(doc_id="d1")),
    lambda: P.create_note(NoteDraft(title="t", body="x")),
    lambda: P.search_notes(NoteSearch(text="x")),
    lambda: P.create_reminder(ReminderDraft(text="t", due_at=T)),
    lambda: P.list_reminders(WINDOW),
]


@pytest.mark.parametrize("call", CALLS)
def test_everything_is_unavailable(call: Callable[[], ToolResult[Any]]) -> None:
    result = call()
    assert result.status is DataStatus.UNAVAILABLE
    assert result.data is None
    assert result.error
