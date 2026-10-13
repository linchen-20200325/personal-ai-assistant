import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai_assistant.audit import (
    MAX_TEXT_LENGTH,
    REDACTED,
    AuditEvent,
    AuditEventType,
    JsonlAuditSink,
    redact,
    redact_text,
)

NOW = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
SECRET = "ya29.super-secret-token-value"  # noqa: S105 - synthetic  gitleaks:allow


def _event(**kwargs: object) -> AuditEvent:
    return AuditEvent.model_validate(
        {"event_type": AuditEventType.ACTION_STARTED, "timestamp": NOW, "actor": "test"} | kwargs
    )


class TestRedaction:
    def test_sensitive_keys_are_masked_at_any_depth(self) -> None:
        params = {
            "access_token": SECRET,
            "nested": {"Authorization": SECRET, "items": [{"api_key": SECRET}]},
            "password": SECRET,
            "body": "private mail body",
            "title": "dentist",
        }
        out = redact(params)
        assert out["access_token"] == REDACTED
        assert out["nested"]["Authorization"] == REDACTED
        assert out["nested"]["items"][0]["api_key"] == REDACTED
        assert out["password"] == REDACTED
        assert out["body"] == REDACTED
        assert out["title"] == "dentist"

    def test_bearer_tokens_and_emails_in_text_are_masked(self) -> None:
        out = redact({"note": f"header Bearer {SECRET} for alice@example.com"})
        assert SECRET not in out["note"]
        assert "alice@" not in out["note"]
        assert "a***@example.com" in out["note"]

    @pytest.mark.parametrize(
        "text",
        [
            f"refresh_token={SECRET} expired",
            f"access_token: {SECRET}",
            f'client_secret="{SECRET}"',
            f"Authorization: Basic {SECRET}",
            f"X-Api-Key={SECRET}",
        ],
    )
    def test_credential_assignments_in_free_text_are_masked(self, text: str) -> None:
        out = redact_text(text)
        assert SECRET not in out
        assert REDACTED in out

    def test_long_text_is_truncated(self) -> None:
        assert len(redact("x" * 10_000)) < MAX_TEXT_LENGTH + 20

    def test_excessive_nesting_is_cut(self) -> None:
        deep: dict[str, object] = {}
        node = deep
        for _ in range(50):
            node["n"] = {}
            node = node["n"]  # type: ignore[assignment]
        assert "[TRUNCATED]" in json.dumps(redact(deep))

    def test_event_redacts_on_construction(self) -> None:
        event = _event(params={"refresh_token": SECRET}, reason=f"failed with Bearer {SECRET}")
        dumped = event.model_dump_json()
        assert SECRET not in dumped


class TestJsonlSink:
    def test_appends_one_redacted_json_line_per_event(self, tmp_path: Path) -> None:
        path = tmp_path / "audit" / "log.jsonl"
        sink = JsonlAuditSink(path)
        sink.record(_event(params={"client_secret": SECRET}))
        sink.record(_event(tool="calendar.list_events"))
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert SECRET not in path.read_text(encoding="utf-8")
        assert json.loads(lines[1])["tool"] == "calendar.list_events"

    def test_file_is_owner_only(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        JsonlAuditSink(path).record(_event())
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
