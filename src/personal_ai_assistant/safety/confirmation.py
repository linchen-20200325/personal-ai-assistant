"""Human confirmation of pending actions.

A ``PendingAction`` pins the exact canonical params by SHA-256 digest. It can
be approved only by presenting that digest (i.e. what the human was shown),
only before it expires, and consumed at most once. Approval is performed by a
human-facing channel (CLI / UI) holding this store; no tool can approve.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Any
from uuid import uuid4

from personal_ai_assistant.audit import AuditEvent, AuditEventType, AuditSink
from personal_ai_assistant.clock import Clock, system_clock
from personal_ai_assistant.domain.risk import RiskLevel


class ConfirmationError(Exception):
    pass


class PendingStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONSUMED = "consumed"


def canonical_json(data: Any) -> str:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def action_digest(tool: str, params_json: str) -> str:
    # Tool names cannot contain "\n" (ToolSpec pattern), so the encoding is unambiguous.
    return hashlib.sha256(f"{tool}\n{params_json}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PendingAction:
    pending_id: str
    action_id: str
    tool: str
    params_json: str
    digest: str
    risk: RiskLevel
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _Record:
    pending: PendingAction
    status: PendingStatus


class ConfirmationStore:
    """In-memory store; pending actions do not survive a restart (fail closed)."""

    def __init__(self, *, audit: AuditSink, clock: Clock = system_clock) -> None:
        self._audit = audit
        self._clock = clock
        self._records: dict[str, _Record] = {}
        self._lock = Lock()

    def create(
        self, *, action_id: str, tool: str, params_json: str, risk: RiskLevel, ttl: timedelta
    ) -> PendingAction:
        now = self._clock()
        pending = PendingAction(
            pending_id=uuid4().hex,
            action_id=action_id,
            tool=tool,
            params_json=params_json,
            digest=action_digest(tool, params_json),
            risk=risk,
            created_at=now,
            expires_at=now + ttl,
        )
        with self._lock:
            self._records[pending.pending_id] = _Record(pending, PendingStatus.PENDING)
        return pending

    def status(self, pending_id: str) -> PendingStatus | None:
        with self._lock:
            record = self._records.get(pending_id)
        return None if record is None else record.status

    def approve(self, pending_id: str, *, digest: str, approver: str) -> None:
        if not approver.strip():
            raise ConfirmationError("approver identity is required")
        with self._lock:
            record = self._require(pending_id, PendingStatus.PENDING)
            if not hmac.compare_digest(digest, record.pending.digest):
                raise ConfirmationError("digest does not match the pending action")
            # Audit first: if it cannot be recorded, the approval does not happen.
            self._record_event(AuditEventType.CONFIRMATION_APPROVED, record.pending, approver)
            self._records[pending_id] = replace(record, status=PendingStatus.APPROVED)

    def reject(self, pending_id: str, *, approver: str) -> None:
        with self._lock:
            record = self._require(pending_id, PendingStatus.PENDING, PendingStatus.APPROVED)
            self._record_event(AuditEventType.CONFIRMATION_REJECTED, record.pending, approver)
            self._records[pending_id] = replace(record, status=PendingStatus.REJECTED)

    def consume(self, pending_id: str) -> PendingAction:
        """Atomically mark an approved, unexpired action as used and return it."""
        with self._lock:
            record = self._require(pending_id, PendingStatus.APPROVED)
            self._records[pending_id] = replace(record, status=PendingStatus.CONSUMED)
            return record.pending

    def _require(self, pending_id: str, *allowed: PendingStatus) -> _Record:
        record = self._records.get(pending_id)
        if record is None:
            raise ConfirmationError("unknown pending action")
        if record.status not in allowed:
            raise ConfirmationError(f"pending action is {record.status}")
        if self._clock() >= record.pending.expires_at:
            raise ConfirmationError("pending action has expired")
        return record

    def _record_event(self, kind: AuditEventType, pending: PendingAction, actor: str) -> None:
        self._audit.record(
            AuditEvent(
                event_type=kind,
                timestamp=self._clock(),
                actor=actor,
                action_id=pending.action_id,
                pending_id=pending.pending_id,
                tool=pending.tool,
                risk=pending.risk,
                digest=pending.digest,
            )
        )
