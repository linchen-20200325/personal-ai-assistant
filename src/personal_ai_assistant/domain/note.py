"""Personal note domain models."""

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_assistant.domain.types import (
    DEFAULT_SEARCH_LIMIT,
    Identifier,
    Instant,
    SearchLimit,
    SearchText,
)


class Note(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    note_id: Identifier
    title: str | None
    body: str
    created_at: Instant | None


class NoteDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)


class NoteSearch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: SearchText | None = None
    limit: SearchLimit = DEFAULT_SEARCH_LIMIT
