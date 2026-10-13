"""Reminder tools."""

from personal_ai_assistant.clock import Clock, system_clock
from personal_ai_assistant.domain.reminder import Reminder, ReminderDraft
from personal_ai_assistant.domain.result import ToolResult
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.domain.types import TimeRange
from personal_ai_assistant.providers.reminder import ReminderProvider
from personal_ai_assistant.tools.base import Tool, ToolSpec


class CreateReminderTool(Tool[ReminderDraft]):
    spec = ToolSpec(
        name="reminders.create",
        description="Create a reminder due at a specific time.",
        base_risk=RiskLevel.WRITE_LOW,
        input_model=ReminderDraft,
    )

    def __init__(self, provider: ReminderProvider, *, clock: Clock = system_clock) -> None:
        self._provider = provider
        self._clock = clock

    def run(self, params: ReminderDraft) -> ToolResult[Reminder]:
        # Checked at execution time: a confirmation may arrive after the due time.
        if params.due_at <= self._clock():
            return ToolResult.failed("due_at is not in the future")
        return self._provider.create_reminder(params)


class ListRemindersTool(Tool[TimeRange]):
    spec = ToolSpec(
        name="reminders.list",
        description="List reminders due within a time range.",
        base_risk=RiskLevel.READ,
        input_model=TimeRange,
    )

    def __init__(self, provider: ReminderProvider) -> None:
        self._provider = provider

    def run(self, params: TimeRange) -> ToolResult[tuple[Reminder, ...]]:
        return self._provider.list_reminders(params)
