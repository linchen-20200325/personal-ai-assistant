"""In-memory reminder provider."""

from personal_ai_assistant.clock import Clock
from personal_ai_assistant.domain.reminder import Reminder, ReminderDraft
from personal_ai_assistant.domain.result import Provenance, ToolResult
from personal_ai_assistant.domain.types import TimeRange

SOURCE = "fake_reminders"


class FakeReminderProvider:
    def __init__(self, *, clock: Clock, outage: str | None = None) -> None:
        self.reminders: list[Reminder] = []
        self.outage = outage
        self._clock = clock

    def create_reminder(self, draft: ReminderDraft) -> ToolResult[Reminder]:
        if self.outage:
            return ToolResult.failed(self.outage)
        reminder = Reminder(
            reminder_id=f"rem-{len(self.reminders) + 1}",
            text=draft.text,
            due_at=draft.due_at,
            created_at=self._clock(),
        )
        self.reminders.append(reminder)
        return ToolResult.ok(
            reminder,
            Provenance(source=SOURCE, retrieved_at=self._clock(), source_ref=reminder.reminder_id),
        )

    def list_reminders(self, window: TimeRange) -> ToolResult[tuple[Reminder, ...]]:
        if self.outage:
            return ToolResult.failed(self.outage)
        hits = tuple(
            sorted(
                (r for r in self.reminders if window.start <= r.due_at < window.end),
                key=lambda r: r.due_at,
            )
        )
        return ToolResult.ok(
            hits, Provenance(source=SOURCE, retrieved_at=self._clock(), source_ref=None)
        )
