"""Note tools."""

from personal_ai_assistant.domain.note import Note, NoteDraft, NoteSearch
from personal_ai_assistant.domain.result import Page, ToolResult
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.providers.note import NoteProvider
from personal_ai_assistant.tools.base import Tool, ToolSpec


class CreateNoteTool(Tool[NoteDraft]):
    spec = ToolSpec(
        name="notes.create",
        description="Save a personal note.",
        base_risk=RiskLevel.WRITE_LOW,
        input_model=NoteDraft,
    )

    def __init__(self, provider: NoteProvider) -> None:
        self._provider = provider

    def run(self, params: NoteDraft) -> ToolResult[Note]:
        return self._provider.create_note(params)


class SearchNotesTool(Tool[NoteSearch]):
    spec = ToolSpec(
        name="notes.search",
        description="Search personal notes by keywords.",
        base_risk=RiskLevel.READ,
        input_model=NoteSearch,
    )

    def __init__(self, provider: NoteProvider) -> None:
        self._provider = provider

    def run(self, params: NoteSearch) -> ToolResult[Page[Note]]:
        return self._provider.search_notes(params)
