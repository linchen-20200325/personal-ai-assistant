"""ConfirmationStore: digest binding, single use, TTL, audit-before-approve."""

from datetime import UTC, datetime, timedelta

import pytest

from personal_ai_assistant.audit import AuditEventType
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.safety.confirmation import (
    ConfirmationError,
    ConfirmationStore,
    PendingAction,
    PendingStatus,
    action_digest,
    canonical_json,
)
from tests.fakes.audit import MemoryAuditSink
from tests.fakes.clock import FakeClock

T0 = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
TTL = timedelta(minutes=10)
HUMAN = "human:test"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def audit() -> MemoryAuditSink:
    return MemoryAuditSink()


@pytest.fixture
def store(audit: MemoryAuditSink, clock: FakeClock) -> ConfirmationStore:
    return ConfirmationStore(audit=audit, clock=clock)


def _create(store: ConfirmationStore, params: str = '{"a":1}') -> PendingAction:
    return store.create(
        action_id="act", tool="demo.send", params_json=params, risk=RiskLevel.WRITE_HIGH, ttl=TTL
    )


class TestDigest:
    def test_canonical_json_ignores_key_order(self) -> None:
        assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})

    def test_canonical_json_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match="JSON compliant"):
            canonical_json({"x": float("nan")})

    def test_digest_binds_tool_and_params(self) -> None:
        base = action_digest("demo.send", '{"a":1}')
        assert base == action_digest("demo.send", '{"a":1}')
        assert base != action_digest("demo.send", '{"a":2}')
        assert base != action_digest("demo.other", '{"a":1}')


class TestApprove:
    def test_requires_exact_digest(self, store: ConfirmationStore) -> None:
        pending = _create(store)
        wrong = action_digest(pending.tool, '{"a":2}')
        with pytest.raises(ConfirmationError, match="digest"):
            store.approve(pending.pending_id, digest=wrong, approver=HUMAN)
        assert store.status(pending.pending_id) is PendingStatus.PENDING

    def test_requires_approver(self, store: ConfirmationStore) -> None:
        pending = _create(store)
        with pytest.raises(ConfirmationError, match="approver"):
            store.approve(pending.pending_id, digest=pending.digest, approver=" ")

    def test_unknown_id(self, store: ConfirmationStore) -> None:
        with pytest.raises(ConfirmationError, match="unknown"):
            store.approve("nope", digest="x", approver=HUMAN)

    def test_is_audited(self, store: ConfirmationStore, audit: MemoryAuditSink) -> None:
        pending = _create(store)
        store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)
        assert audit.types() == [AuditEventType.CONFIRMATION_APPROVED]
        assert audit.events[0].actor == HUMAN
        assert audit.events[0].digest == pending.digest

    def test_audit_failure_blocks_approval(
        self, store: ConfirmationStore, audit: MemoryAuditSink
    ) -> None:
        pending = _create(store)
        audit.fail = True
        with pytest.raises(OSError, match="audit"):
            store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)
        assert store.status(pending.pending_id) is PendingStatus.PENDING

    def test_cannot_approve_twice(self, store: ConfirmationStore) -> None:
        pending = _create(store)
        store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)
        with pytest.raises(ConfirmationError, match="approved"):
            store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)


class TestConsume:
    def test_single_use(self, store: ConfirmationStore) -> None:
        pending = _create(store)
        store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)
        assert store.consume(pending.pending_id) == pending
        with pytest.raises(ConfirmationError, match="consumed"):
            store.consume(pending.pending_id)

    def test_requires_approval(self, store: ConfirmationStore) -> None:
        pending = _create(store)
        with pytest.raises(ConfirmationError, match="pending"):
            store.consume(pending.pending_id)

    def test_rejected_cannot_be_consumed_or_approved(self, store: ConfirmationStore) -> None:
        pending = _create(store)
        store.reject(pending.pending_id, approver=HUMAN)
        with pytest.raises(ConfirmationError, match="rejected"):
            store.consume(pending.pending_id)
        with pytest.raises(ConfirmationError, match="rejected"):
            store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)

    def test_approved_can_still_be_rejected(self, store: ConfirmationStore) -> None:
        pending = _create(store)
        store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)
        store.reject(pending.pending_id, approver=HUMAN)
        with pytest.raises(ConfirmationError, match="rejected"):
            store.consume(pending.pending_id)


class TestTtl:
    def test_expires_at_is_created_plus_ttl(self, store: ConfirmationStore) -> None:
        pending = _create(store)
        assert pending.expires_at - pending.created_at == TTL

    def test_cannot_approve_after_expiry(self, store: ConfirmationStore, clock: FakeClock) -> None:
        pending = _create(store)
        clock.advance(TTL)  # boundary: now == expires_at counts as expired
        with pytest.raises(ConfirmationError, match="expired"):
            store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)

    def test_approved_but_expired_cannot_be_consumed(
        self, store: ConfirmationStore, clock: FakeClock
    ) -> None:
        pending = _create(store)
        clock.advance(TTL - timedelta(seconds=1))
        store.approve(pending.pending_id, digest=pending.digest, approver=HUMAN)
        clock.advance(timedelta(seconds=1))
        with pytest.raises(ConfirmationError, match="expired"):
            store.consume(pending.pending_id)

    def test_expired_records_are_swept_on_create(
        self, store: ConfirmationStore, clock: FakeClock
    ) -> None:
        old = _create(store)
        clock.advance(TTL)
        fresh = _create(store)
        assert store.status(old.pending_id) is None
        assert store.status(fresh.pending_id) is PendingStatus.PENDING
