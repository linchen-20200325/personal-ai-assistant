"""Value types shared by all domain models.

All datetimes must be timezone-aware; naive values are rejected at validation
time so that "Wednesday afternoon" can never silently resolve in the wrong zone.
"""

import re
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StringConstraints,
    model_validator,
)

_ISO_DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")


def _strict_datetime_input(value: Any) -> Any:
    # Lax mode would read numbers / numeric strings as Unix timestamps (silently
    # UTC); accept only datetime objects and ISO-8601 strings.
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and _ISO_DATE_PREFIX.match(value):
        return value
    raise ValueError("datetime must be an ISO-8601 string with a UTC offset")


def _whole_minute_offset(value: datetime) -> datetime:
    # Sub-minute offsets do not survive JSON round-trips, which would break digests.
    offset = value.utcoffset()
    if offset is not None and offset.total_seconds() % 60:
        raise ValueError("UTC offset must be a whole number of minutes")
    return value


# Timezone-aware, ISO input only, digest-stable.
Instant = Annotated[
    AwareDatetime, BeforeValidator(_strict_datetime_input), AfterValidator(_whole_minute_offset)
]

EmailAddress = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
]


class TimeRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: Instant
    end: Instant

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self
