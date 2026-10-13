"""E-mail domain models. Message content is untrusted external data."""

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_assistant.domain.types import (
    DEFAULT_SEARCH_LIMIT,
    EmailAddress,
    Identifier,
    Instant,
    SearchLimit,
    SearchText,
    TimeRange,
)


class EmailMessage(BaseModel):
    """A message as reported by the provider. ``None`` means unknown or not fetched
    (e.g. ``body_text`` is ``None`` in search results that carry no body)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: Identifier
    thread_id: Identifier | None
    subject: str | None
    sender: EmailAddress | None
    recipients: tuple[EmailAddress, ...] | None
    received_at: Instant | None
    snippet: str | None
    body_text: str | None
    labels: tuple[str, ...] | None
    is_unread: bool | None


class EmailSearch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: SearchText | None = None
    window: TimeRange | None = None
    limit: SearchLimit = DEFAULT_SEARCH_LIMIT


class MessageRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: Identifier


class EmailDraft(BaseModel):
    """A draft to be saved, not sent."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    to: tuple[EmailAddress, ...] = Field(min_length=1, max_length=50)
    cc: tuple[EmailAddress, ...] = Field(default=(), max_length=50)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=50_000)


class SavedDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: Identifier
    draft: EmailDraft
