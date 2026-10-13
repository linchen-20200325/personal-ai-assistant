"""Tool results that keep fetched data separate from missing data.

``None`` always means *unknown*. A provider that cannot answer must return an
ERROR / UNAVAILABLE result, never an empty collection presented as success:
"no events tomorrow" (OK + empty) and "could not read the calendar" (ERROR)
must stay distinguishable for the AI layer.
"""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class DataStatus(StrEnum):
    OK = "ok"  # complete, verified data (may be an empty collection)
    PARTIAL = "partial"  # usable data with known gaps listed in ``missing``
    UNAVAILABLE = "unavailable"  # source not reachable / not configured / not authorised
    ERROR = "error"  # the call failed


class Provenance(BaseModel):
    """Where and when a piece of data was obtained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    retrieved_at: AwareDatetime
    source_ref: str | None


class ToolResult[T](BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DataStatus
    data: T | None
    provenance: Provenance | None
    missing: tuple[str, ...] = ()
    error: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.status in (DataStatus.OK, DataStatus.PARTIAL):
            if self.data is None or self.provenance is None:
                raise ValueError(f"{self.status} result requires data and provenance")
            if self.error is not None:
                raise ValueError(f"{self.status} result must not carry an error")
            if (self.status is DataStatus.PARTIAL) != bool(self.missing):
                raise ValueError("'missing' must be non-empty exactly when status is partial")
        else:
            if self.data is not None:
                raise ValueError(f"{self.status} result must not carry data")
            if not self.error:
                raise ValueError(f"{self.status} result requires an error reason")
            if self.missing:
                raise ValueError(f"{self.status} result must not list missing fields")
        return self

    @classmethod
    def ok(cls, data: T, provenance: Provenance) -> Self:
        return cls(status=DataStatus.OK, data=data, provenance=provenance)

    @classmethod
    def partial(cls, data: T, provenance: Provenance, missing: tuple[str, ...]) -> Self:
        return cls(status=DataStatus.PARTIAL, data=data, provenance=provenance, missing=missing)

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        status: DataStatus = DataStatus.ERROR,
        provenance: Provenance | None = None,
    ) -> Self:
        if status in (DataStatus.OK, DataStatus.PARTIAL):
            raise ValueError("failed() requires ERROR or UNAVAILABLE status")
        return cls(status=status, data=None, provenance=provenance, error=reason)

    @property
    def succeeded(self) -> bool:
        return self.status in (DataStatus.OK, DataStatus.PARTIAL)


class Page[T](BaseModel):
    """A bounded slice of a larger result set.

    ``has_more`` tells the analysis layer whether the list is complete: True or
    False when the provider knows, ``None`` when it cannot tell. It has no
    default, so an adapter must state it explicitly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[T, ...]
    has_more: bool | None
