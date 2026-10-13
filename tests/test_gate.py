"""Gate decision matrix and risk escalation (pure functions)."""

import itertools
from datetime import timedelta

import pytest
from pydantic import ValidationError

from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.safety.gate import Policy, Verdict, decide, effective_risk

TOOL = "demo.tool"
R = RiskLevel
A, C, D = Verdict.ALLOW, Verdict.CONFIRM, Verdict.DENY

# (risk, writes_enabled, tool allowlisted) -> verdict, every combination listed.
MATRIX = {
    (R.READ, False, False): A, (R.READ, False, True): A,
    (R.READ, True, False): A, (R.READ, True, True): A,
    (R.DRAFT, False, False): A, (R.DRAFT, False, True): A,
    (R.DRAFT, True, False): A, (R.DRAFT, True, True): A,
    (R.WRITE_LOW, False, False): D, (R.WRITE_LOW, False, True): D,
    (R.WRITE_LOW, True, False): C, (R.WRITE_LOW, True, True): A,
    (R.WRITE_HIGH, False, False): D, (R.WRITE_HIGH, False, True): D,
    (R.WRITE_HIGH, True, False): C, (R.WRITE_HIGH, True, True): C,
}  # fmt: skip


def test_matrix_is_exhaustive() -> None:
    assert set(MATRIX) == set(itertools.product(RiskLevel, (False, True), (False, True)))


@pytest.mark.parametrize(("case", "expected"), list(MATRIX.items()), ids=str)
def test_decision_matrix(case: tuple[RiskLevel, bool, bool], expected: Verdict) -> None:
    risk, writes_enabled, allowlisted = case
    policy = Policy(
        writes_enabled=writes_enabled,
        auto_write_low=frozenset({TOOL}) if allowlisted else frozenset(),
    )
    assert decide(risk, TOOL, policy) is expected


def test_write_high_is_never_allowed_by_any_policy() -> None:
    for writes_enabled, ttl in itertools.product((False, True), (1, 86_400)):
        policy = Policy(
            writes_enabled=writes_enabled,
            auto_write_low=frozenset({TOOL}),
            confirmation_ttl=timedelta(seconds=ttl),
        )
        assert decide(RiskLevel.WRITE_HIGH, TOOL, policy) is not Verdict.ALLOW


@pytest.mark.parametrize(
    ("base", "assessed"), list(itertools.product(RiskLevel, RiskLevel)), ids=str
)
def test_effective_risk_only_escalates(base: RiskLevel, assessed: RiskLevel) -> None:
    result = effective_risk(base, assessed)
    assert result >= base
    assert result == max(base, assessed)


class TestPolicy:
    def test_defaults_are_safe(self) -> None:
        policy = Policy()
        assert policy.writes_enabled is False
        assert policy.auto_write_low == frozenset()

    def test_has_no_knob_for_write_high(self) -> None:
        for key in ("auto_write_high", "skip_confirmation", "write_high_requires_confirmation"):
            with pytest.raises(ValidationError):
                Policy.model_validate({key: True})

    @pytest.mark.parametrize("seconds", [0, -1, 86_401])
    def test_ttl_bounds(self, seconds: int) -> None:
        with pytest.raises(ValidationError):
            Policy(confirmation_ttl=timedelta(seconds=seconds))
