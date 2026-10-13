"""Permission gate: a pure function from (risk, tool, policy) to a verdict."""

from datetime import timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_assistant.domain.risk import RiskLevel

MAX_CONFIRMATION_TTL = timedelta(days=1)


class Verdict(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


class Policy(BaseModel):
    """User-adjustable policy. Nothing here can make WRITE_HIGH skip confirmation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Global kill switch for WRITE_LOW and WRITE_HIGH.
    writes_enabled: bool = False
    # WRITE_LOW tools that may run without confirmation. Validated against the
    # registered tools by ActionExecutor; WRITE_HIGH tools are rejected there.
    auto_write_low: frozenset[str] = frozenset()
    confirmation_ttl: timedelta = Field(
        default=timedelta(minutes=10), gt=timedelta(0), le=MAX_CONFIRMATION_TTL
    )


def effective_risk(base: RiskLevel, assessed: RiskLevel) -> RiskLevel:
    """Dynamic assessment may raise the declared risk, never lower it."""
    return max(base, assessed)


def decide(risk: RiskLevel, tool: str, policy: Policy) -> Verdict:
    if risk.is_write and not policy.writes_enabled:
        return Verdict.DENY
    if risk <= RiskLevel.DRAFT:
        return Verdict.ALLOW
    if risk is RiskLevel.WRITE_LOW and tool in policy.auto_write_low:
        return Verdict.ALLOW
    # Non-allowlisted WRITE_LOW and every WRITE_HIGH. No policy reaches past here.
    return Verdict.CONFIRM
