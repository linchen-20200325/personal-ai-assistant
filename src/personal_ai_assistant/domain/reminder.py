"""Reminder domain models (storage only; delivery is out of scope)."""

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_assistant.domain.types import Identifier, Instant, SingleLine


class Reminder(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reminder_id: Identifier
    text: str = Field(min_length=1)
    due_at: Instant
    created_at: Instant | None


class ReminderDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    text: SingleLine = Field(min_length=1, max_length=500)
    due_at: Instant
