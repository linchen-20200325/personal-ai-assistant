"""Settings from environment variables (see .env.example).

Core settings hold no secrets. Provider credentials belong to their adapters
(e.g. ``adapters/anthropic_model.py``), are typed as ``SecretStr`` and read
from the environment / a secret manager, never from source code.
"""

import os
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator

from personal_ai_assistant.safety.gate import Policy

DEFAULT_AUDIT_LOG_PATH = Path("var/audit.jsonl")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: Policy
    audit_log_path: Path
    # IANA zone the planner resolves relative dates in; None = host local zone.
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("unknown IANA time zone") from None
        return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def get(name: str) -> str | None:
        value = env.get(f"ASSISTANT_{name}", "").strip()
        return value or None

    policy: dict[str, Any] = {}
    if (writes := get("WRITES_ENABLED")) is not None:
        policy["writes_enabled"] = writes
    if (auto := get("AUTO_WRITE_LOW")) is not None:
        policy["auto_write_low"] = frozenset(n.strip() for n in auto.split(",") if n.strip())
    if (ttl := get("CONFIRMATION_TTL_SECONDS")) is not None:
        policy["confirmation_ttl"] = timedelta(seconds=int(ttl))
    return Settings(
        policy=Policy.model_validate(policy),
        audit_log_path=Path(get("AUDIT_LOG_PATH") or DEFAULT_AUDIT_LOG_PATH),
        timezone=get("TIMEZONE"),
    )
