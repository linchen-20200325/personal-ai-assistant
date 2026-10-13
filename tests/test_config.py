from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_ai_assistant.config import DEFAULT_AUDIT_LOG_PATH, load_settings


def test_defaults_are_safe() -> None:
    settings = load_settings({})
    assert settings.policy.writes_enabled is False
    assert settings.policy.auto_write_low == frozenset()
    assert settings.policy.confirmation_ttl == timedelta(minutes=10)
    assert settings.audit_log_path == DEFAULT_AUDIT_LOG_PATH


def test_blank_values_fall_back_to_defaults() -> None:
    settings = load_settings({"ASSISTANT_WRITES_ENABLED": " ", "ASSISTANT_AUTO_WRITE_LOW": ""})
    assert settings.policy.writes_enabled is False
    assert settings.policy.auto_write_low == frozenset()


def test_parses_values() -> None:
    settings = load_settings(
        {
            "ASSISTANT_WRITES_ENABLED": "true",
            "ASSISTANT_AUTO_WRITE_LOW": " notes.create , calendar.create_event,,",
            "ASSISTANT_CONFIRMATION_TTL_SECONDS": "120",
            "ASSISTANT_AUDIT_LOG_PATH": "/tmp/x/audit.jsonl",  # noqa: S108
        }
    )
    assert settings.policy.writes_enabled is True
    assert settings.policy.auto_write_low == {"notes.create", "calendar.create_event"}
    assert settings.policy.confirmation_ttl == timedelta(seconds=120)
    assert settings.audit_log_path == Path("/tmp/x/audit.jsonl")  # noqa: S108


@pytest.mark.parametrize(
    "env",
    [
        {"ASSISTANT_WRITES_ENABLED": "maybe"},
        {"ASSISTANT_CONFIRMATION_TTL_SECONDS": "ten"},
        {"ASSISTANT_CONFIRMATION_TTL_SECONDS": "0"},
        {"ASSISTANT_CONFIRMATION_TTL_SECONDS": "86401"},
    ],
)
def test_invalid_values_fail_at_startup(env: dict[str, str]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        load_settings(env)


def test_reads_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSISTANT_WRITES_ENABLED", "1")
    assert load_settings().policy.writes_enabled is True
