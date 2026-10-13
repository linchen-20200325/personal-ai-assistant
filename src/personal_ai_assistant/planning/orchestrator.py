"""Orchestrator: request -> planner -> local validation -> ActionExecutor.

The orchestrator holds no tool instances and cannot approve anything. It asks
the planner for proposals, rejects the whole plan if any proposal names an
unknown tool or carries invalid params (no partial execution of a broken plan),
records the plan in the audit trail, and hands each proposal to
``ActionExecutor`` which classifies risk and applies the permission gate.
Human confirmation of pending actions happens in the human channel (CLI).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from pydantic import ValidationError

from personal_ai_assistant.audit import AuditEvent, AuditEventType, AuditSink
from personal_ai_assistant.clock import Clock, system_clock
from personal_ai_assistant.planning.contracts import (
    MAX_REQUEST_CHARS,
    InvalidPlanError,
    Plan,
    Planner,
    PlanningError,
    PlanningRequest,
)
from personal_ai_assistant.safety.executor import ActionExecutor, ExecutionResult, Outcome
from personal_ai_assistant.tools.base import PlannedAction
from personal_ai_assistant.validation import validation_summary


@dataclass(frozen=True, slots=True)
class Proposal:
    plan_id: str
    plan: Plan | None
    error: str | None = None


class Orchestrator:
    def __init__(
        self,
        planner: Planner,
        executor: ActionExecutor,
        *,
        audit: AuditSink,
        clock: Clock = system_clock,
        local_time: Clock | None = None,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._audit = audit
        self._clock = clock
        # Wall-clock time in the user's zone, shown to the planner. Defaults to
        # the host's local zone (a known value, not a guess).
        self._local_time = local_time or (lambda: self._clock().astimezone())

    def propose(self, text: str) -> Proposal:
        plan_id = uuid4().hex
        text = text.strip()
        if not text:
            return self._fail(plan_id, AuditEventType.PLAN_REJECTED, "empty request")
        if len(text) > MAX_REQUEST_CHARS:
            return self._fail(
                plan_id, AuditEventType.PLAN_REJECTED, f"request longer than {MAX_REQUEST_CHARS}"
            )
        try:
            specs = self._executor.specs()
            now: datetime = self._local_time()
            plan = self._planner.plan(PlanningRequest(text=text, tools=specs, now=now))
        except InvalidPlanError as exc:
            return self._fail(plan_id, AuditEventType.PLAN_REJECTED, str(exc))
        except PlanningError as exc:
            return self._fail(plan_id, AuditEventType.PLAN_FAILED, str(exc))
        except Exception as exc:
            # A planner bug must not crash the session or leak its message.
            return self._fail(
                plan_id, AuditEventType.PLAN_FAILED, f"planner raised {type(exc).__name__}"
            )
        if not isinstance(plan, Plan):
            return self._fail(plan_id, AuditEventType.PLAN_REJECTED, "planner returned no Plan")

        by_name = {spec.name: spec for spec in specs}
        for index, action in enumerate(plan.actions):
            spec = by_name.get(action.tool)
            if spec is None:
                return self._fail(
                    plan_id, AuditEventType.PLAN_REJECTED, f"actions.{index}: unknown tool"
                )
            try:
                spec.input_model.model_validate(action.params)
            except ValidationError as exc:
                return self._fail(
                    plan_id,
                    AuditEventType.PLAN_REJECTED,
                    f"actions.{index}.params: {validation_summary(exc)}",
                )
        try:
            self._record(
                AuditEventType.PLAN_CREATED,
                plan_id,
                params={
                    "tools": [action.tool for action in plan.actions],
                    "questions": len(plan.questions),
                },
            )
        except Exception:
            # No audit trail, no plan.
            return Proposal(plan_id, None, "audit unavailable; plan discarded")
        return Proposal(plan_id, plan)

    def execute(self, proposal: Proposal) -> tuple[ExecutionResult, ...]:
        """Submit every proposed action to the executor (which gates each one)."""
        if proposal.plan is None:
            return ()
        results: list[ExecutionResult] = []
        for action in proposal.plan.actions:
            try:
                result = self._executor.submit(
                    PlannedAction(tool=action.tool, params=action.params),
                    plan_id=proposal.plan_id,
                )
            except Exception as exc:
                # Keep reporting what already ran; never let one action hide the others.
                result = ExecutionResult(
                    uuid4().hex,
                    Outcome.FAILED,
                    action.tool,
                    reason=f"executor raised {type(exc).__name__}",
                )
            results.append(result)
        return tuple(results)

    def _fail(self, plan_id: str, kind: AuditEventType, reason: str) -> Proposal:
        try:
            self._record(kind, plan_id, reason=reason)
        except Exception:
            reason = f"{reason}; audit unavailable"
        return Proposal(plan_id, None, reason)

    def _record(
        self,
        kind: AuditEventType,
        plan_id: str,
        *,
        reason: str | None = None,
        params: dict[str, object] | None = None,
    ) -> None:
        # The request text and model output are never written: they may hold
        # personal data. Tool names, counts and validation summaries only.
        self._audit.record(
            AuditEvent(
                event_type=kind,
                timestamp=self._clock(),
                actor="orchestrator",
                plan_id=plan_id,
                reason=reason,
                params=params,
            )
        )
