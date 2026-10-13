"""Placeholder for every provider until real adapters exist (B4).

Each call returns ``UNAVAILABLE`` with a reason, so the planner and the user
see "not connected" instead of an empty result that could pass for real data.
"""

from personal_ai_assistant.domain.calendar import CalendarEvent, EventDraft
from personal_ai_assistant.domain.document import (
    DocumentContent,
    DocumentMeta,
    DocumentSearch,
    ReadDocument,
)
from personal_ai_assistant.domain.email import EmailDraft, EmailMessage, EmailSearch, SavedDraft
from personal_ai_assistant.domain.note import Note, NoteDraft, NoteSearch
from personal_ai_assistant.domain.reminder import Reminder, ReminderDraft
from personal_ai_assistant.domain.result import DataStatus, Page, ToolResult
from personal_ai_assistant.domain.types import TimeRange


class UnconfiguredProvider:
    """Implements every provider protocol; never touches any service."""

    def __init__(self, reason: str = "no provider configured for this data source") -> None:
        self._reason = reason

    def _unavailable[T](self) -> ToolResult[T]:
        return ToolResult[T].failed(self._reason, status=DataStatus.UNAVAILABLE)

    def list_events(self, window: TimeRange) -> ToolResult[tuple[CalendarEvent, ...]]:
        return self._unavailable()

    def create_event(self, draft: EventDraft) -> ToolResult[CalendarEvent]:
        return self._unavailable()

    def search_messages(self, query: EmailSearch) -> ToolResult[Page[EmailMessage]]:
        return self._unavailable()

    def get_message(self, message_id: str) -> ToolResult[EmailMessage]:
        return self._unavailable()

    def create_draft(self, draft: EmailDraft) -> ToolResult[SavedDraft]:
        return self._unavailable()

    def search_documents(self, query: DocumentSearch) -> ToolResult[Page[DocumentMeta]]:
        return self._unavailable()

    def read_document(self, request: ReadDocument) -> ToolResult[DocumentContent]:
        return self._unavailable()

    def create_note(self, draft: NoteDraft) -> ToolResult[Note]:
        return self._unavailable()

    def search_notes(self, query: NoteSearch) -> ToolResult[Page[Note]]:
        return self._unavailable()

    def create_reminder(self, draft: ReminderDraft) -> ToolResult[Reminder]:
        return self._unavailable()

    def list_reminders(self, window: TimeRange) -> ToolResult[tuple[Reminder, ...]]:
        return self._unavailable()
