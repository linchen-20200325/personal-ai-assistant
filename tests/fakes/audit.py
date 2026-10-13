from personal_ai_assistant.audit import AuditEvent, AuditEventType


class MemoryAuditSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[AuditEvent] = []
        self.fail = fail
        self.fail_on: set[AuditEventType] = set()

    def record(self, event: AuditEvent) -> None:
        if self.fail or event.event_type in self.fail_on:
            raise OSError("audit storage unavailable")
        self.events.append(event)

    def types(self) -> list[AuditEventType]:
        return [e.event_type for e in self.events]
