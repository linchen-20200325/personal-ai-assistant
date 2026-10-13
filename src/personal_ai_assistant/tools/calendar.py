"""Calendar tools."""

from personal_ai_assistant.domain.calendar import CalendarEvent, EventDraft
from personal_ai_assistant.domain.result import ToolResult
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.domain.types import TimeRange
from personal_ai_assistant.providers.calendar import CalendarProvider
from personal_ai_assistant.tools.base import Tool, ToolSpec


class ListEventsTool(Tool[TimeRange]):
    spec = ToolSpec(
        name="calendar.list_events",
        description="List calendar events overlapping a time range.",
        base_risk=RiskLevel.READ,
        input_model=TimeRange,
    )

    def __init__(self, provider: CalendarProvider) -> None:
        self._provider = provider

    def run(self, params: TimeRange) -> ToolResult[tuple[CalendarEvent, ...]]:
        return self._provider.list_events(params)


class CreateEventTool(Tool[EventDraft]):
    spec = ToolSpec(
        name="calendar.create_event",
        description=(
            "Create a calendar event. Adding attendees sends them invitations "
            "and is treated as a high-risk outbound action."
        ),
        base_risk=RiskLevel.WRITE_LOW,
        input_model=EventDraft,
    )

    def __init__(self, provider: CalendarProvider) -> None:
        self._provider = provider

    def assess_risk(self, params: EventDraft) -> RiskLevel:
        # Invitations leave the user's account: outbound side effect.
        return RiskLevel.WRITE_HIGH if params.attendees else RiskLevel.WRITE_LOW

    def run(self, params: EventDraft) -> ToolResult[CalendarEvent]:
        return self._provider.create_event(params)
