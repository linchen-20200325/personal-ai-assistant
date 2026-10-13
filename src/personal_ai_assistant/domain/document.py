"""Document domain models. Document content is untrusted external data."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, Strict

from personal_ai_assistant.domain.types import (
    DEFAULT_SEARCH_LIMIT,
    Identifier,
    Instant,
    SearchLimit,
    SearchText,
)

DEFAULT_MAX_CHARS = 20_000


class DocumentMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: Identifier
    title: str | None
    mime_type: str | None
    modified_at: Instant | None
    web_url: str | None


class DocumentContent(BaseModel):
    """Extracted plain text. ``truncated`` must be stated so a summary never
    presents part of a document as the whole."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: Identifier
    title: str | None
    mime_type: str | None
    text: str
    truncated: bool


class DocumentSearch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: SearchText | None = None
    limit: SearchLimit = DEFAULT_SEARCH_LIMIT


class ReadDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: Identifier
    max_chars: Annotated[int, Strict()] = Field(default=DEFAULT_MAX_CHARS, ge=1, le=200_000)
