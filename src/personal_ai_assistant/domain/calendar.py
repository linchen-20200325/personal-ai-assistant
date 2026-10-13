"""Calendar domain models.

All datetimes must be timezone-aware; naive values are rejected at validation
time so that "Wednesday afternoon" can never silently resolve in the wrong zone.
"""

from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

EmailAddress = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
]


class TimeRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class CalendarEvent(BaseModel):
    """An event as reported by a provider. ``None`` means the provider did not say.

    No field has a default: an adapter must state every value explicitly, so a
    missing value can never be filled in by accident.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1)
    title: str | None
    start: AwareDatetime
    end: AwareDatetime | None
    location: str | None
    attendees: tuple[EmailAddress, ...] | None


class EventDraft(BaseModel):
    """Requested new event (tool input)."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    start: AwareDatetime
    end: AwareDatetime
    location: str | None = Field(default=None, max_length=500)
    attendees: tuple[EmailAddress, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self
