"""Note provider contract."""

from typing import Protocol

from personal_ai_assistant.domain.note import Note, NoteDraft, NoteSearch
from personal_ai_assistant.domain.result import Page, ToolResult


class NoteProvider(Protocol):
    def create_note(self, draft: NoteDraft) -> ToolResult[Note]: ...

    def search_notes(self, query: NoteSearch) -> ToolResult[Page[Note]]:
        """Newest first, at most ``query.limit`` items."""
        ...
