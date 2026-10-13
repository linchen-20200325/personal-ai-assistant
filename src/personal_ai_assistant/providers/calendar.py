"""Calendar provider contract."""

from typing import Protocol

from personal_ai_assistant.domain.calendar import CalendarEvent, EventDraft, TimeRange
from personal_ai_assistant.domain.result import ToolResult


class CalendarProvider(Protocol):
    """Data access only: fetch, normalise, attach provenance. No analysis.

    Expected failures (auth, network, quota) are returned as
    ``ToolResult.failed(...)``, never raised and never reported as an empty
    success.
    """

    def list_events(self, window: TimeRange) -> ToolResult[tuple[CalendarEvent, ...]]:
        """Events overlapping ``window``, ordered by start."""
        ...

    def create_event(self, draft: EventDraft) -> ToolResult[CalendarEvent]: ...
