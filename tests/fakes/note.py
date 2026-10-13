"""In-memory note provider."""

from personal_ai_assistant.clock import Clock
from personal_ai_assistant.domain.note import Note, NoteDraft, NoteSearch
from personal_ai_assistant.domain.result import Page, Provenance, ToolResult

SOURCE = "fake_notes"


class FakeNoteProvider:
    def __init__(self, *, clock: Clock, outage: str | None = None) -> None:
        self.notes: list[Note] = []
        self.outage = outage
        self._clock = clock

    def _provenance(self, ref: str | None = None) -> Provenance:
        return Provenance(source=SOURCE, retrieved_at=self._clock(), source_ref=ref)

    def create_note(self, draft: NoteDraft) -> ToolResult[Note]:
        if self.outage:
            return ToolResult.failed(self.outage)
        note = Note(
            note_id=f"note-{len(self.notes) + 1}",
            title=draft.title,
            body=draft.body,
            created_at=self._clock(),
        )
        self.notes.append(note)
        return ToolResult.ok(note, self._provenance(note.note_id))

    def search_notes(self, query: NoteSearch) -> ToolResult[Page[Note]]:
        if self.outage:
            return ToolResult.failed(self.outage)
        needle = query.text.casefold() if query.text else None
        hits = [
            n
            for n in reversed(self.notes)
            if needle is None or needle in f"{n.title or ''} {n.body}".casefold()
        ]
        page = Page(items=tuple(hits[: query.limit]), has_more=len(hits) > query.limit)
        return ToolResult.ok(page, self._provenance())
