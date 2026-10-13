"""Append-only audit trail.

Redaction happens inside ``AuditEvent`` validation, so no code path can write
unredacted params or reasons to a sink. Redaction is best effort (sensitive key
names, ``key=value`` / ``key: value`` / JSON-style credential assignments,
bearer tokens, e-mail local parts, length cap); secrets hidden in free text
without such markers are not detectable and must not be passed in.
"""

import os
import re
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from personal_ai_assistant.domain.risk import RiskLevel

REDACTED = "[REDACTED]"
MAX_TEXT_LENGTH = 256
_MAX_DEPTH = 8
_SENSITIVE_KEY = re.compile(
    r"token|secret|passw|authori[sz]|api[_-]?key|cookie|credential|session|body|content"
    r"|subject|^text$",
    re.IGNORECASE,
)
# Standalone bearer tokens ("Bearer <long token>").
_BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)
# Credential assignments in free text: key=value, key: value, "key": "value",
# including "Authorization: Basic xyz". Quantifiers are bounded to avoid
# catastrophic backtracking; input length is capped before matching anyway.
_ASSIGNMENT = re.compile(
    r"(?<![\w.-])([\w.-]{0,64}?"
    r"(?:token|secret|passw|pwd|api[_-]?key|credential|cookie|session[_-]?id|authori[sz]ation)"
    r"[\w.-]{0,64})"
    r"([\"']?\s*[=:]\s*)"
    r"((?:(?:bearer|basic|digest)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+))",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"([^@\s\"'<>(),;:]+)@([^@\s\"'<>(),;:]+)")


def redact_text(text: str) -> str:
    # Truncate first: bounds regex work regardless of input size.
    truncated = len(text) > MAX_TEXT_LENGTH
    text = text[:MAX_TEXT_LENGTH]
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _EMAIL.sub(lambda m: f"{m.group(1)[0]}***@{m.group(2)}", text)
    return text + "...[truncated]" if truncated else text


def redact(value: Any, _depth: int = 0) -> Any:
    """Return a JSON-safe copy of ``value`` with sensitive content masked."""
    if _depth > _MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            str(k): REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v, _depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value))


class AuditEventType(StrEnum):
    ACTION_DENIED = "action.denied"
    ACTION_PENDING = "action.pending"
    ACTION_STARTED = "action.started"
    ACTION_EXECUTED = "action.executed"
    ACTION_FAILED = "action.failed"
    CONFIRMATION_APPROVED = "confirmation.approved"
    CONFIRMATION_REJECTED = "confirmation.rejected"


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: AuditEventType
    timestamp: AwareDatetime
    actor: str = Field(min_length=1)
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    action_id: str | None = None
    pending_id: str | None = None
    tool: str | None = None
    risk: RiskLevel | None = None
    digest: str | None = None
    params: dict[str, Any] | None = None
    reason: str | None = None

    @field_validator("params", mode="after")
    @classmethod
    def _redact_params(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else redact(value)

    @field_validator("tool", "pending_id", "reason", mode="after")
    @classmethod
    def _redact_text(cls, value: str | None) -> str | None:
        return None if value is None else redact_text(value)


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None:
        """Persist ``event``; must raise if it cannot, so callers can fail closed."""
        ...


class JsonlAuditSink:
    """One JSON object per line, appended and fsynced; file created with mode 0600."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    def record(self, event: AuditEvent) -> None:
        line = event.model_dump_json() + "\n"
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
