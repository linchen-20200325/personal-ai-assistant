"""Settings from environment variables (see .env.example).

No secrets are defined yet; provider credentials arrive with their adapters
and must be typed as ``SecretStr`` and read from the environment / a secret
manager, never from source code.
"""

import os
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from personal_ai_assistant.safety.gate import Policy

DEFAULT_AUDIT_LOG_PATH = Path("var/audit.jsonl")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: Policy
    audit_log_path: Path


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
    )
