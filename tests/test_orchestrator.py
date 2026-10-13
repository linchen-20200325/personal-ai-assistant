"""Orchestrator: plans are validated as a whole, gated by the executor, and audited."""

from datetime import UTC, datetime
from typing import Any

import pytest

from personal_ai_assistant.audit import AuditEventType
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.planning.contracts import (
    MAX_REQUEST_CHARS,
    InvalidPlanError,
    ModelUnavailableError,
    Plan,
    ProposedAction,
)
from personal_ai_assistant.planning.orchestrator import Orchestrator
from personal_ai_assistant.safety.confirmation import ConfirmationStore
from personal_ai_assistant.safety.executor import ActionExecutor, Outcome
from personal_ai_assistant.safety.gate import Policy
from personal_ai_assistant.tools.calendar import CreateEventTool, ListEventsTool
from tests.fakes.audit import MemoryAuditSink
from tests.fakes.calendar import FakeCalendarProvider
from tests.fakes.clock import FakeClock
from tests.fakes.planner import ScriptedPlanner

T0 = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
WINDOW = {"start": "2026-10-14T12:00:00+08:00", "end": "2026-10-14T18:00:00+08:00"}
DRAFT = {
    "title": "Dentist",
    "start": "2026-10-14T15:00:00+08:00",
    "end": "2026-10-14T16:00:00+08:00",
}
INVITE = DRAFT | {"attendees": ["guest@example.com"]}
REQUEST = "明天下午幫我看行程，private detail 0912-345-678"

S = AuditEventType


def plan(*actions: tuple[str, dict[str, Any]], questions: tuple[str, ...] = ()) -> Plan:
    return Plan(
        summary="s",
        actions=tuple(ProposedAction(tool=t, params=p, rationale="r") for t, p in actions),
        questions=questions,
    )


class Env:
    def __init__(self, *script: Plan | BaseException, policy: Policy | None = None) -> None:
        self.clock = FakeClock(T0)
        self.audit = MemoryAuditSink()
        self.provider = FakeCalendarProvider(clock=self.clock)
        self.confirmations = ConfirmationStore(audit=self.audit, clock=self.clock)
        self.executor = ActionExecutor(
            [ListEventsTool(self.provider), CreateEventTool(self.provider)],
            policy=policy or Policy(writes_enabled=True),
            confirmations=self.confirmations,
            audit=self.audit,
            clock=self.clock,
        )
        self.planner = ScriptedPlanner(*script)
        self.orchestrator = Orchestrator(
            self.planner, self.executor, audit=self.audit, clock=self.clock
        )

    def run(self, text: str = REQUEST) -> tuple[Any, tuple[Any, ...]]:
        proposal = self.orchestrator.propose(text)
        return proposal, self.orchestrator.execute(proposal)


def test_read_executes_and_write_waits_for_human() -> None:
    env = Env(plan(("calendar.list_events", WINDOW), ("calendar.create_event", DRAFT)))
    proposal, results = env.run()
    assert proposal.plan is not None
    assert [r.outcome for r in results] == [Outcome.EXECUTED, Outcome.PENDING_CONFIRMATION]
    assert env.provider.created == []
    assert env.audit.types() == [
        S.PLAN_CREATED,
        S.ACTION_STARTED,
        S.ACTION_EXECUTED,
        S.ACTION_PENDING,
    ]
    assert {e.plan_id for e in env.audit.events} == {proposal.plan_id}


def test_planner_receives_specs_and_aware_local_time() -> None:
    env = Env(plan())
    env.run("  hello  ")
    sent = env.planner.requests[0]
    assert sent.text == "hello"
    assert {s.name for s in sent.tools} == {"calendar.list_events", "calendar.create_event"}
    assert sent.now.utcoffset() is not None


@pytest.mark.parametrize(
    "policy", [Policy(writes_enabled=True, auto_write_low=frozenset({"calendar.create_event"}))]
)
def test_llm_cannot_skip_confirmation_for_write_high(policy: Policy) -> None:
    # Even with the tool allow-listed for WRITE_LOW, invitations escalate to WRITE_HIGH.
    env = Env(plan(("calendar.create_event", INVITE)), policy=policy)
    _, (result,) = env.run()
    assert result.outcome is Outcome.PENDING_CONFIRMATION
    assert result.risk is RiskLevel.WRITE_HIGH
    assert env.provider.created == []


def test_writes_disabled_denies_llm_write() -> None:
    env = Env(plan(("calendar.create_event", DRAFT)), policy=Policy())
    _, (result,) = env.run()
    assert result.outcome is Outcome.DENIED
    assert env.provider.created == []


@pytest.mark.parametrize(
    ("actions", "reason"),
    [
        (
            (("calendar.list_events", WINDOW), ("email.send", {"to": "x@example.com"})),
            "actions.1: unknown tool",
        ),
        (
            (("calendar.list_events", WINDOW), ("calendar.list_events", {"start": "tomorrow"})),
            "actions.1.params",
        ),
        (
            (
                (
                    "calendar.list_events",
                    {"start": "2026-10-14T12:00:00"} | {"end": "2026-10-14T18:00:00"},
                ),
            ),
            "timezone_aware",
        ),
        ((("calendar.list_events", WINDOW | {"risk": "READ"}),), "<extra>"),
    ],
)
def test_any_invalid_action_rejects_whole_plan(
    actions: tuple[tuple[str, dict[str, Any]], ...], reason: str
) -> None:
    env = Env(plan(*actions))
    proposal, results = env.run()
    assert proposal.plan is None
    assert reason in (proposal.error or "")
    assert results == ()
    assert env.audit.types() == [S.PLAN_REJECTED]  # nothing ran, not even the valid read


@pytest.mark.parametrize(
    ("error", "kind", "reason"),
    [
        (InvalidPlanError("model output is not valid JSON"), S.PLAN_REJECTED, "not valid JSON"),
        (ModelUnavailableError("model request timed out"), S.PLAN_FAILED, "timed out"),
        (RuntimeError("boom sk-ant-secret-xyz"), S.PLAN_FAILED, "planner raised RuntimeError"),
    ],
)
def test_planner_failures_are_audited_and_contained(
    error: BaseException, kind: AuditEventType, reason: str
) -> None:
    env = Env(error)
    proposal, results = env.run()
    assert proposal.plan is None
    assert reason in (proposal.error or "")
    assert "sk-ant" not in (proposal.error or "")
    assert results == ()
    assert env.audit.types() == [kind]


def test_non_plan_return_is_rejected() -> None:
    env = Env({"summary": "x"})  # type: ignore[arg-type]
    proposal, _ = env.run()
    assert proposal.error == "planner returned no Plan"


@pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_REQUEST_CHARS + 1)])
def test_bad_request_never_reaches_planner(text: str) -> None:
    env = Env()
    proposal, _ = env.run(text)
    assert proposal.plan is None
    assert env.planner.requests == []
    assert env.audit.types() == [S.PLAN_REJECTED]


def test_audit_failure_discards_plan() -> None:
    env = Env(plan(("calendar.list_events", WINDOW)))
    env.audit.fail_on = {S.PLAN_CREATED}
    proposal, results = env.run()
    assert proposal.plan is None
    assert results == ()
    assert env.provider.created == []


def test_request_text_and_planner_text_never_audited() -> None:
    env = Env(plan(("calendar.list_events", WINDOW), questions=("which day?",)))
    env.run()
    dumped = " ".join(e.model_dump_json() for e in env.audit.events)
    assert "0912-345-678" not in dumped
    assert "which day?" not in dumped
    created = env.audit.events[0]
    assert created.params == {"tools": ["calendar.list_events"], "questions": 1}


def test_pending_audit_failure_discards_pending_and_keeps_going() -> None:
    env = Env(plan(("calendar.create_event", DRAFT), ("calendar.list_events", WINDOW)))
    env.audit.fail_on = {S.ACTION_PENDING}
    _, results = env.run()
    assert [r.outcome for r in results] == [Outcome.FAILED, Outcome.EXECUTED]
    assert "not queued" in (results[0].reason or "")
    # Nothing approvable was left behind.
    assert env.confirmations._records == {}


def test_deny_audit_failure_is_reported_not_raised() -> None:
    env = Env(plan(("calendar.create_event", DRAFT)), policy=Policy())
    env.audit.fail_on = {S.ACTION_DENIED}
    _, (result,) = env.run()
    assert result.outcome is Outcome.DENIED
    assert result.reason == "WRITE_LOW actions are disabled by policy; audit unavailable"


def test_executor_crash_is_contained_per_action(monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env(plan(("calendar.list_events", WINDOW), ("calendar.list_events", WINDOW)))
    calls = []
    original = env.executor.submit

    def flaky(action: Any, *, plan_id: str | None = None) -> Any:
        calls.append(action)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return original(action, plan_id=plan_id)

    monkeypatch.setattr(env.executor, "submit", flaky)
    _, results = env.run()
    assert [r.outcome for r in results] == [Outcome.FAILED, Outcome.EXECUTED]
    assert results[0].reason == "executor raised RuntimeError"


def test_clock_failure_is_audited_as_plan_failure() -> None:
    env = Env(plan())

    def broken() -> datetime:
        raise OSError("clock")

    env.orchestrator = Orchestrator(
        env.planner, env.executor, audit=env.audit, clock=env.clock, local_time=broken
    )
    proposal, _ = env.run()
    assert proposal.error == "planner raised OSError"
    assert env.audit.types() == [S.PLAN_FAILED]
