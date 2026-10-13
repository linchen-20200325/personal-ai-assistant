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
    Field,
    Strict,
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

# Bare addr-spec only: no display names, quotes, separators or comments, so one
# value can never smuggle in a second recipient. IDN domains must be punycode.
EmailAddress = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=254,
        pattern=r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$",
    ),
]

# Single-line free text (subjects, titles): no control characters such as CR/LF.
SingleLine = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[^\x00-\x1f\x7f]*$")
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


# Opaque provider identifier: printable ASCII, no whitespace.
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=512, pattern=r"^[!-~]+$")]

# Plain keywords; adapters translate them into provider query syntax.
SearchText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]

DEFAULT_SEARCH_LIMIT = 20
SearchLimit = Annotated[int, Strict(), Field(ge=1, le=100)]
