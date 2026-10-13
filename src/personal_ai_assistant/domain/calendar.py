"""Calendar domain models."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_assistant.domain.types import EmailAddress, Instant


class CalendarEvent(BaseModel):
    """An event as reported by a provider. ``None`` means the provider did not say.

    No field has a default: an adapter must state every value explicitly, so a
    missing value can never be filled in by accident.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1)
    title: str | None
    start: Instant
    end: Instant | None
    location: str | None
    attendees: tuple[EmailAddress, ...] | None


class EventDraft(BaseModel):
    """Requested new event (tool input)."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    start: Instant
    end: Instant
    location: str | None = Field(default=None, max_length=500)
    attendees: tuple[EmailAddress, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self
