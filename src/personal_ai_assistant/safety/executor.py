"""The single chokepoint between planner proposals and side effects.

Every tool invocation goes: resolve -> validate -> assess risk -> gate ->
(confirm) -> audit -> run -> audit. ``ActionExecutor`` exposes no way to
approve a pending action and no access to tool instances.
"""

import hmac
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from personal_ai_assistant.audit import AuditEvent, AuditEventType, AuditSink
from personal_ai_assistant.clock import Clock, system_clock
from personal_ai_assistant.domain.result import ToolResult
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.safety.confirmation import (
    ConfirmationError,
    ConfirmationStore,
    PendingAction,
    action_digest,
    canonical_json,
)
from personal_ai_assistant.safety.gate import Policy, Verdict, decide, effective_risk
from personal_ai_assistant.tools.base import PlannedAction, Tool, ToolSpec


class Outcome(StrEnum):
    EXECUTED = "executed"
    PENDING_CONFIRMATION = "pending_confirmation"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    action_id: str
    outcome: Outcome
    tool: str | None
    risk: RiskLevel | None = None
    result: ToolResult[Any] | None = None
    pending: PendingAction | None = None
    reason: str | None = None


class ActionExecutor:
    def __init__(
        self,
        tools: Iterable[Tool[Any]],
        *,
        policy: Policy,
        confirmations: ConfirmationStore,
        audit: AuditSink,
        clock: Clock = system_clock,
    ) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools:
            if tool.spec.name in self._tools:
                raise ValueError(f"duplicate tool {tool.spec.name!r}")
            self._tools[tool.spec.name] = tool
        for name in sorted(policy.auto_write_low):
            known = self._tools.get(name)
            if known is None:
                raise ValueError(f"auto_write_low names unknown tool {name!r}")
            if known.spec.base_risk is not RiskLevel.WRITE_LOW:
                raise ValueError(
                    f"auto_write_low may only list WRITE_LOW tools; "
                    f"{name!r} is {known.spec.base_risk.name}"
                )
        self._policy = policy
        self._confirmations = confirmations
        self._audit = audit
        self._clock = clock

    def specs(self) -> tuple[ToolSpec, ...]:
        """What a planner may propose. Specs are data; tools stay private."""
        return tuple(tool.spec for tool in self._tools.values())

    def submit(self, action: PlannedAction) -> ExecutionResult:
        action_id = uuid4().hex
        tool = self._tools.get(action.tool)
        if tool is None:
            return self._deny(action_id, action.tool, None, "unknown tool")
        try:
            params = tool.spec.input_model.model_validate(action.params)
        except ValidationError as exc:
            return self._deny(action_id, action.tool, None, f"invalid params: {_summary(exc)}")
        try:
            risk = self._assess(tool, params)
        except Exception as exc:
            return self._deny(
                action_id, action.tool, None, f"risk assessment failed: {type(exc).__name__}"
            )
        params_json = canonical_json(params.model_dump(mode="json"))
        verdict = decide(risk, action.tool, self._policy)
        if verdict is Verdict.DENY:
            return self._deny(
                action_id, action.tool, risk, f"{risk.name} actions are disabled by policy"
            )
        if verdict is Verdict.CONFIRM:
            pending = self._confirmations.create(
                action_id=action_id,
                tool=action.tool,
                params_json=params_json,
                risk=risk,
                ttl=self._policy.confirmation_ttl,
            )
            self._record(
                AuditEventType.ACTION_PENDING,
                action_id,
                action.tool,
                risk,
                params,
                pending_id=pending.pending_id,
                digest=pending.digest,
            )
            return ExecutionResult(
                action_id, Outcome.PENDING_CONFIRMATION, action.tool, risk, pending=pending
            )
        return self._run(
            action_id, tool, params, risk, digest=action_digest(action.tool, params_json)
        )

    def execute_approved(self, pending_id: str) -> ExecutionResult:
        """Run a human-approved pending action exactly once, re-verifying everything."""
        try:
            pending = self._confirmations.consume(pending_id)
        except ConfirmationError as exc:
            return self._deny(uuid4().hex, None, None, str(exc), pending_id=pending_id)

        def deny(reason: str, risk: RiskLevel | None = pending.risk) -> ExecutionResult:
            return self._deny(pending.action_id, pending.tool, risk, reason, pending_id=pending_id)

        tool = self._tools.get(pending.tool)
        if tool is None:
            return deny("tool is no longer registered")
        try:
            params = tool.spec.input_model.model_validate_json(pending.params_json)
        except ValidationError as exc:
            return deny(f"stored params no longer valid: {_summary(exc)}")
        digest = action_digest(pending.tool, canonical_json(params.model_dump(mode="json")))
        if not hmac.compare_digest(digest, pending.digest):
            return deny("params do not match the approved digest")
        try:
            risk = self._assess(tool, params)
        except Exception as exc:
            return deny(f"risk assessment failed: {type(exc).__name__}")
        if risk > pending.risk:
            return deny("risk increased since approval; confirm again", risk)
        if decide(risk, pending.tool, self._policy) is Verdict.DENY:
            return deny(f"{risk.name} actions are disabled by policy", risk)
        return self._run(pending.action_id, tool, params, risk, digest, pending_id=pending_id)

    @staticmethod
    def _assess(tool: Tool[Any], params: BaseModel) -> RiskLevel:
        assessed = tool.assess_risk(params)
        if not isinstance(assessed, RiskLevel):
            raise TypeError("assess_risk must return a RiskLevel")
        return effective_risk(tool.spec.base_risk, assessed)

    def _run(
        self,
        action_id: str,
        tool: Tool[Any],
        params: BaseModel,
        risk: RiskLevel,
        digest: str,
        pending_id: str | None = None,
    ) -> ExecutionResult:
        name = tool.spec.name
        try:
            self._record(
                AuditEventType.ACTION_STARTED,
                action_id,
                name,
                risk,
                params,
                pending_id=pending_id,
                digest=digest,
            )
        except Exception:
            # No audit trail, no side effect.
            return ExecutionResult(
                action_id, Outcome.FAILED, name, risk, reason="audit unavailable; not executed"
            )
        try:
            result: object = tool.run(params)
        except Exception as exc:
            result = ToolResult[Any].failed(f"tool raised {type(exc).__name__}")
        if not isinstance(result, ToolResult):
            result = ToolResult[Any].failed(f"tool returned {type(result).__name__}")
        outcome = Outcome.EXECUTED if result.succeeded else Outcome.FAILED
        self._record(
            AuditEventType.ACTION_EXECUTED if result.succeeded else AuditEventType.ACTION_FAILED,
            action_id,
            name,
            risk,
            None,
            pending_id=pending_id,
            digest=digest,
            reason=result.error,
        )
        return ExecutionResult(action_id, outcome, name, risk, result=result, reason=result.error)

    def _deny(
        self,
        action_id: str,
        tool: str | None,
        risk: RiskLevel | None,
        reason: str,
        *,
        pending_id: str | None = None,
    ) -> ExecutionResult:
        self._record(
            AuditEventType.ACTION_DENIED,
            action_id,
            tool,
            risk,
            None,
            pending_id=pending_id,
            reason=reason,
        )
        return ExecutionResult(action_id, Outcome.DENIED, tool, risk, reason=reason)

    def _record(
        self,
        kind: AuditEventType,
        action_id: str,
        tool: str | None,
        risk: RiskLevel | None,
        params: BaseModel | None,
        *,
        pending_id: str | None = None,
        digest: str | None = None,
        reason: str | None = None,
    ) -> None:
        self._audit.record(
            AuditEvent(
                event_type=kind,
                timestamp=self._clock(),
                actor="executor",
                action_id=action_id,
                pending_id=pending_id,
                tool=tool,
                risk=risk,
                digest=digest,
                params=None if params is None else params.model_dump(mode="json"),
                reason=reason,
            )
        )


def _summary(exc: ValidationError) -> str:
    """Error locations and types only; input values never reach the audit log."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['type']}"
        for err in exc.errors(include_url=False, include_input=False, include_context=False)
    )
