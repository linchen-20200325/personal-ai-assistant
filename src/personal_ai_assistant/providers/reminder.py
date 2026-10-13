"""Reminder provider contract (storage; delivery is out of scope)."""

from typing import Protocol

from personal_ai_assistant.domain.reminder import Reminder, ReminderDraft
from personal_ai_assistant.domain.result import ToolResult
from personal_ai_assistant.domain.types import TimeRange


class ReminderProvider(Protocol):
    def create_reminder(self, draft: ReminderDraft) -> ToolResult[Reminder]: ...

    def list_reminders(self, window: TimeRange) -> ToolResult[tuple[Reminder, ...]]:
        """Reminders due within ``window``, ordered by ``due_at``."""
        ...
