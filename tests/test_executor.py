"""End-to-end safety behaviour of ActionExecutor, including bypass attempts."""

import ast
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

import personal_ai_assistant
from personal_ai_assistant.audit import AuditEventType, JsonlAuditSink
from personal_ai_assistant.domain.calendar import CalendarEvent
from personal_ai_assistant.domain.result import DataStatus, Provenance, ToolResult
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.safety.confirmation import (
    ConfirmationError,
    ConfirmationStore,
    PendingStatus,
)
from personal_ai_assistant.safety.executor import ActionExecutor, ExecutionResult, Outcome
from personal_ai_assistant.safety.gate import Policy
from personal_ai_assistant.tools.base import PlannedAction, Tool, ToolSpec
from personal_ai_assistant.tools.calendar import CreateEventTool, ListEventsTool
from tests.fakes.audit import MemoryAuditSink
from tests.fakes.calendar import FakeCalendarProvider
from tests.fakes.clock import FakeClock

T0 = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
HUMAN = "human:test"
SECRET = "sk-synthetic-secret-123456"  # noqa: S105 - synthetic  gitleaks:allow
WINDOW = {"start": "2026-10-14T00:00:00+08:00", "end": "2026-10-15T00:00:00+08:00"}
DRAFT = {
    "title": "Dentist",
    "start": "2026-10-14T15:00:00+08:00",
    "end": "2026-10-14T16:00:00+08:00",
}
INVITE = DRAFT | {"attendees": ["guest@example.com"]}

S = AuditEventType


# --- synthetic tools used to probe the executor -----------------------------


class Msg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class SendTool(Tool[Msg]):
    """WRITE_HIGH whose assess_risk tries to downgrade itself."""

    spec = ToolSpec(
        name="demo.send", description="d", base_risk=RiskLevel.WRITE_HIGH, input_model=Msg
    )

    def __init__(self) -> None:
        self.calls = 0

    def assess_risk(self, params: Msg) -> RiskLevel:
        return RiskLevel.READ

    def run(self, params: Msg) -> ToolResult[Any]:
        self.calls += 1
        return ToolResult.ok(
            params.text, Provenance(source="demo", retrieved_at=T0, source_ref=None)
        )


class Secretive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_token: str
    query: str


class SecretiveRead(Tool[Secretive]):
    spec = ToolSpec(
        name="demo.lookup", description="d", base_risk=RiskLevel.READ, input_model=Secretive
    )

    def run(self, params: Secretive) -> ToolResult[Any]:
        return ToolResult.ok(1, Provenance(source="demo", retrieved_at=T0, source_ref=None))


@dataclass
class Misbehaving(Tool[Msg]):
    """Configurable failure modes."""

    raise_in_run: bool = False
    returns: object = None
    risk: object = RiskLevel.WRITE_LOW
    calls: int = 0

    spec = ToolSpec(
        name="demo.flaky", description="d", base_risk=RiskLevel.WRITE_LOW, input_model=Msg
    )

    def assess_risk(self, params: Msg) -> RiskLevel:
        if isinstance(self.risk, Exception):
            raise self.risk
        return self.risk  # type: ignore[return-value]

    def run(self, params: Msg) -> ToolResult[Any]:
        self.calls += 1
        if self.raise_in_run:
            raise RuntimeError(f"leaky message containing {SECRET}")
        return self.returns  # type: ignore[return-value]


# --- harness ----------------------------------------------------------------


@dataclass
class Harness:
    executor: ActionExecutor
    store: ConfirmationStore
    audit: MemoryAuditSink
    clock: FakeClock
    calendar: FakeCalendarProvider

    def submit(self, tool: str, params: dict[str, Any]) -> ExecutionResult:
        return self.executor.submit(PlannedAction(tool=tool, params=params))

    def approve(self, result: ExecutionResult) -> str:
        assert result.outcome is Outcome.PENDING_CONFIRMATION
        assert result.pending is not None
        self.store.approve(result.pending.pending_id, digest=result.pending.digest, approver=HUMAN)
        return result.pending.pending_id


def make(
    *, writes: bool = True, auto: frozenset[str] = frozenset(), extra: tuple[Tool[Any], ...] = ()
) -> Harness:
    clock = FakeClock(T0)
    audit = MemoryAuditSink()
    store = ConfirmationStore(audit=audit, clock=clock)
    calendar = FakeCalendarProvider(clock=clock)
    tools: list[Tool[Any]] = [ListEventsTool(calendar), CreateEventTool(calendar), *extra]
    executor = ActionExecutor(
        tools,
        policy=Policy(writes_enabled=writes, auto_write_low=auto),
        confirmations=store,
        audit=audit,
        clock=clock,
    )
    return Harness(executor, store, audit, clock, calendar)


# --- happy paths -------------------------------------------------------------


def test_read_runs_without_confirmation_and_is_audited() -> None:
    h = make()
    result = h.submit("calendar.list_events", WINDOW)
    assert result.outcome is Outcome.EXECUTED
    assert result.result is not None
    assert result.result.status is DataStatus.OK
    assert h.audit.types() == [S.ACTION_STARTED, S.ACTION_EXECUTED]


def test_write_low_requires_confirmation_by_default() -> None:
    h = make()
    result = h.submit("calendar.create_event", DRAFT)
    assert result.outcome is Outcome.PENDING_CONFIRMATION
    assert result.risk is RiskLevel.WRITE_LOW
    assert h.calendar.created == []
    executed = h.executor.execute_approved(h.approve(result))
    assert executed.outcome is Outcome.EXECUTED
    assert len(h.calendar.created) == 1
    assert h.audit.types() == [
        S.ACTION_PENDING,
        S.CONFIRMATION_APPROVED,
        S.ACTION_STARTED,
        S.ACTION_EXECUTED,
    ]
    assert len({e.action_id for e in h.audit.events}) == 1  # one traceable action


def test_allowlisted_write_low_runs_directly() -> None:
    h = make(auto=frozenset({"calendar.create_event"}))
    assert h.submit("calendar.create_event", DRAFT).outcome is Outcome.EXECUTED
    assert len(h.calendar.created) == 1


def test_writes_disabled_denies_every_write() -> None:
    h = make(writes=False)
    for params in (DRAFT, INVITE):
        assert h.submit("calendar.create_event", params).outcome is Outcome.DENIED
    assert h.submit("calendar.list_events", WINDOW).outcome is Outcome.EXECUTED
    assert h.calendar.created == []


# --- 1. WRITE_HIGH cannot be downgraded by config -----------------------------


def test_config_cannot_allowlist_write_high_tool() -> None:
    with pytest.raises(ValueError, match="only list WRITE_LOW"):
        make(auto=frozenset({"demo.send"}), extra=(SendTool(),))


def test_config_cannot_allowlist_unknown_tool() -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        make(auto=frozenset({"calendar.delete_everything"}))


def test_allowlisted_tool_still_confirms_when_escalated_to_write_high() -> None:
    h = make(auto=frozenset({"calendar.create_event"}))
    result = h.submit("calendar.create_event", INVITE)
    assert result.outcome is Outcome.PENDING_CONFIRMATION
    assert result.risk is RiskLevel.WRITE_HIGH
    assert h.calendar.created == []


# --- 2. dynamic risk only escalates ------------------------------------------


def test_assess_risk_cannot_downgrade_declared_risk() -> None:
    send = SendTool()
    h = make(extra=(send,))
    result = h.submit("demo.send", {"text": "hi"})
    assert result.outcome is Outcome.PENDING_CONFIRMATION
    assert result.risk is RiskLevel.WRITE_HIGH
    assert send.calls == 0


@pytest.mark.parametrize("bad_risk", [ValueError("boom"), 0, "READ", None])
def test_broken_risk_assessment_fails_closed(bad_risk: object) -> None:
    tool = Misbehaving(risk=bad_risk)
    h = make(extra=(tool,))
    result = h.submit("demo.flaky", {"text": "x"})
    assert result.outcome is Outcome.DENIED
    assert tool.calls == 0


def test_risk_increase_after_approval_requires_new_confirmation() -> None:
    tool = Misbehaving(risk=RiskLevel.WRITE_LOW)
    h = make(extra=(tool,))
    pending_id = h.approve(h.submit("demo.flaky", {"text": "x"}))
    tool.risk = RiskLevel.WRITE_HIGH
    result = h.executor.execute_approved(pending_id)
    assert result.outcome is Outcome.DENIED
    assert "risk increased" in (result.reason or "")
    assert tool.calls == 0


# --- 3. approval bound to the exact params digest ----------------------------


def test_tampered_stored_params_are_rejected_at_execution() -> None:
    h = make()
    pending_id = h.approve(h.submit("calendar.create_event", DRAFT))
    record = h.store._records[pending_id]
    forged = record.pending.params_json.replace("Dentist", "Wire money")
    h.store._records[pending_id] = replace(
        record, pending=replace(record.pending, params_json=forged)
    )
    result = h.executor.execute_approved(pending_id)
    assert result.outcome is Outcome.DENIED
    assert "digest" in (result.reason or "")
    assert h.calendar.created == []


def test_approval_of_one_action_cannot_be_used_for_another() -> None:
    h = make()
    first = h.submit("calendar.create_event", DRAFT)
    second = h.submit("calendar.create_event", DRAFT | {"title": "Something else"})
    assert first.pending is not None
    assert second.pending is not None
    assert first.pending.digest != second.pending.digest
    with pytest.raises(ConfirmationError, match="digest"):
        h.store.approve(second.pending.pending_id, digest=first.pending.digest, approver=HUMAN)
    assert h.executor.execute_approved(second.pending.pending_id).outcome is Outcome.DENIED


def test_executed_params_equal_approved_params() -> None:
    h = make()
    h.executor.execute_approved(h.approve(h.submit("calendar.create_event", INVITE)))
    (event,) = h.calendar.created
    assert event.title == "Dentist"
    assert event.attendees == ("guest@example.com",)


def test_corrupted_stored_params_are_rejected_at_execution() -> None:
    h = make()
    pending_id = h.approve(h.submit("calendar.create_event", DRAFT))
    record = h.store._records[pending_id]
    h.store._records[pending_id] = replace(
        record, pending=replace(record.pending, params_json='{"title": 1}')
    )
    result = h.executor.execute_approved(pending_id)
    assert result.outcome is Outcome.DENIED
    assert "no longer valid" in (result.reason or "")
    assert h.calendar.created == []


# --- 4. approval is single-use and expires -----------------------------------


def test_approval_cannot_be_replayed() -> None:
    h = make()
    pending_id = h.approve(h.submit("calendar.create_event", DRAFT))
    assert h.executor.execute_approved(pending_id).outcome is Outcome.EXECUTED
    replay = h.executor.execute_approved(pending_id)
    assert replay.outcome is Outcome.DENIED
    assert len(h.calendar.created) == 1


def test_expired_approval_is_denied() -> None:
    h = make()
    pending_id = h.approve(h.submit("calendar.create_event", DRAFT))
    h.clock.advance(Policy().confirmation_ttl)
    result = h.executor.execute_approved(pending_id)
    assert result.outcome is Outcome.DENIED
    assert "expired" in (result.reason or "")
    assert h.calendar.created == []


def test_unapproved_or_unknown_pending_is_denied() -> None:
    h = make()
    result = h.submit("calendar.create_event", DRAFT)
    assert result.pending is not None
    for pending_id in (result.pending.pending_id, "does-not-exist"):
        assert h.executor.execute_approved(pending_id).outcome is Outcome.DENIED
    assert h.calendar.created == []


def test_disabling_writes_after_approval_blocks_execution() -> None:
    h = make()
    pending_id = h.approve(h.submit("calendar.create_event", DRAFT))
    locked = ActionExecutor(
        [CreateEventTool(h.calendar)],
        policy=Policy(writes_enabled=False),
        confirmations=h.store,
        audit=h.audit,
        clock=h.clock,
    )
    assert locked.execute_approved(pending_id).outcome is Outcome.DENIED
    assert h.calendar.created == []


def test_risk_assessment_failure_after_approval_is_denied() -> None:
    tool = Misbehaving(risk=RiskLevel.WRITE_LOW)
    h = make(extra=(tool,))
    pending_id = h.approve(h.submit("demo.flaky", {"text": "x"}))
    tool.risk = RuntimeError("boom")
    assert h.executor.execute_approved(pending_id).outcome is Outcome.DENIED
    assert tool.calls == 0


def test_tool_unregistered_after_approval_is_denied() -> None:
    h = make()
    pending_id = h.approve(h.submit("calendar.create_event", DRAFT))
    reduced = ActionExecutor(
        [ListEventsTool(h.calendar)],
        policy=Policy(writes_enabled=True),
        confirmations=h.store,
        audit=h.audit,
        clock=h.clock,
    )
    result = reduced.execute_approved(pending_id)
    assert result.outcome is Outcome.DENIED
    assert result.reason == "tool is no longer registered"


def test_duplicate_tool_names_are_rejected() -> None:
    """A second registration must not silently shadow a tool's declared risk."""
    with pytest.raises(ValueError, match="duplicate tool"):
        make(extra=(ListEventsTool(FakeCalendarProvider(clock=FakeClock(T0))),))


# --- 5/6/8. unknown tools, extra fields, naive datetimes ---------------------


def test_unknown_tool_is_denied() -> None:
    h = make()
    result = h.submit("calendar.delete_everything", {})
    assert result.outcome is Outcome.DENIED
    assert result.reason == "unknown tool"
    assert h.audit.types() == [S.ACTION_DENIED]


@pytest.mark.parametrize(
    "params",
    [
        WINDOW | {"risk": "READ"},
        WINDOW | {"approved": True},
        DRAFT | {"send_invites_to": "everyone@example.com"},
    ],
)
def test_extra_fields_are_denied(params: dict[str, Any]) -> None:
    h = make()
    tool = "calendar.list_events" if "title" not in params else "calendar.create_event"
    result = h.submit(tool, params)
    assert result.outcome is Outcome.DENIED
    assert "extra_forbidden" in (result.reason or "")
    assert h.calendar.created == []


def test_naive_datetime_is_denied() -> None:
    h = make()
    naive = {"start": "2026-10-14T09:00:00", "end": "2026-10-14T18:00:00"}
    for tool, params in (("calendar.list_events", naive), ("calendar.create_event", DRAFT | naive)):
        result = h.submit(tool, params)
        assert result.outcome is Outcome.DENIED
        assert "timezone_aware" in (result.reason or "")


# --- 7. provider failure is never an empty success ---------------------------


def test_provider_outage_is_a_failure_with_no_data() -> None:
    h = make()
    h.calendar.outage = "upstream timeout"
    result = h.submit("calendar.list_events", WINDOW)
    assert result.outcome is Outcome.FAILED
    assert result.result is not None
    assert result.result.data is None
    assert result.result.status is DataStatus.ERROR
    assert h.audit.types() == [S.ACTION_STARTED, S.ACTION_FAILED]


@pytest.mark.parametrize("returns", [[], (), None, {"status": "ok"}])
def test_tool_returning_non_result_is_a_failure(returns: object) -> None:
    h = make(auto=frozenset({"demo.flaky"}), extra=(Misbehaving(returns=returns),))
    result = h.submit("demo.flaky", {"text": "x"})
    assert result.outcome is Outcome.FAILED
    assert result.result is not None
    assert result.result.data is None


def test_tool_exception_is_a_failure_without_leaking_its_message() -> None:
    h = make(auto=frozenset({"demo.flaky"}), extra=(Misbehaving(raise_in_run=True),))
    result = h.submit("demo.flaky", {"text": "x"})
    assert result.outcome is Outcome.FAILED
    assert result.reason == "tool raised RuntimeError"
    assert SECRET not in str([e.model_dump() for e in h.audit.events])


def test_empty_calendar_is_a_verified_success() -> None:
    h = make()
    result = h.submit("calendar.list_events", WINDOW)
    assert result.result is not None
    assert result.result.data == ()
    assert result.result.provenance is not None


# --- 9. audit redaction and fail-closed audit --------------------------------


def test_audit_log_on_disk_never_contains_secrets(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    clock = FakeClock(T0)
    sink = JsonlAuditSink(path)
    executor = ActionExecutor(
        [SecretiveRead()],
        policy=Policy(),
        confirmations=ConfirmationStore(audit=sink, clock=clock),
        audit=sink,
        clock=clock,
    )
    ok = executor.submit(
        PlannedAction(tool="demo.lookup", params={"api_token": SECRET, "query": "q"})
    )
    bad = executor.submit(
        PlannedAction(tool="demo.lookup", params={"api_token": "x", "query": "q", "leak": SECRET})
    )
    assert ok.outcome is Outcome.EXECUTED
    assert bad.outcome is Outcome.DENIED
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "[REDACTED]" in text


def test_audit_failure_prevents_execution() -> None:
    h = make(auto=frozenset({"calendar.create_event"}))
    h.audit.fail = True
    result = h.submit("calendar.create_event", DRAFT)
    assert result.outcome is Outcome.FAILED
    assert result.reason == "audit unavailable; not executed"
    assert h.calendar.created == []


def test_completion_audit_failure_reports_instead_of_raising() -> None:
    """The side effect already happened; raising would invite a duplicate retry."""
    h = make(auto=frozenset({"calendar.create_event"}))
    h.audit.fail_on = {S.ACTION_EXECUTED}
    result = h.submit("calendar.create_event", DRAFT)
    assert result.outcome is Outcome.EXECUTED
    assert result.reason == "completion audit failed"
    assert len(h.calendar.created) == 1


def test_provider_error_text_is_redacted_for_audit_and_planner() -> None:
    h = make()
    h.calendar.outage = f"401 from upstream: access_token={SECRET} Authorization: Basic {SECRET}"
    result = h.submit("calendar.list_events", WINDOW)
    assert result.outcome is Outcome.FAILED
    assert result.result is not None
    for text in (result.reason, result.result.error, str([e.reason for e in h.audit.events])):
        assert text is not None
        assert SECRET not in text


def test_rejected_extra_key_names_are_not_logged() -> None:
    h = make()
    result = h.submit("calendar.list_events", WINDOW | {SECRET: 1})
    assert result.outcome is Outcome.DENIED
    assert result.reason == "invalid params: <extra>: extra_forbidden"
    assert SECRET not in str([e.model_dump() for e in h.audit.events])


def test_malformed_pending_id_is_denied_without_echo() -> None:
    h = make()
    result = h.executor.execute_approved("A" * 10_000)
    assert result.outcome is Outcome.DENIED
    assert result.reason == "malformed pending id"
    assert h.audit.events[-1].pending_id is None


def test_audit_masks_attendee_emails() -> None:
    h = make()
    h.submit("calendar.create_event", INVITE)
    params = h.audit.events[0].params
    assert params is not None
    assert params["attendees"] == ["g***@example.com"]


# --- 10. no planner path around the executor ---------------------------------


def test_executor_public_surface_has_no_approval_or_tool_access() -> None:
    h = make()
    public = {name for name in dir(h.executor) if not name.startswith("_")}
    assert public == {"specs", "submit", "execute_approved"}
    assert all(name.startswith("_") for name in vars(h.executor))
    assert all(isinstance(spec, ToolSpec) for spec in h.executor.specs())


def test_planned_action_schema_is_only_tool_and_params() -> None:
    assert set(PlannedAction.model_fields) == {"tool", "params"}


def test_resubmitting_a_write_never_executes_it() -> None:
    h = make()
    results = [h.submit("calendar.create_event", DRAFT) for _ in range(3)]
    assert {r.outcome for r in results} == {Outcome.PENDING_CONFIRMATION}
    assert len({r.pending.pending_id for r in results if r.pending}) == 3
    assert all(
        h.store.status(r.pending.pending_id) is PendingStatus.PENDING for r in results if r.pending
    )
    assert h.calendar.created == []


def test_only_the_executor_invokes_tool_run() -> None:
    """Architecture guard: no other module in the package calls ``<x>.run(...)``."""
    root = Path(personal_ai_assistant.__file__).parent
    callers = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    }
    assert callers == {"safety/executor.py"}


def test_calendar_event_results_are_typed() -> None:
    h = make()
    h.calendar.events.append(
        CalendarEvent(
            event_id="e1",
            title=None,
            start=datetime(2026, 10, 14, 1, 0, tzinfo=UTC),
            end=None,
            location=None,
            attendees=None,
        )
    )
    result = h.submit("calendar.list_events", WINDOW)
    assert result.result is not None
    assert isinstance(result.result.data, tuple)
    assert result.result.data[0].title is None  # unknown stays unknown
