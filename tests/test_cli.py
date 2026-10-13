"""CLI human channel: rendering from params_json, explicit 'yes', fail-closed paths."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from personal_ai_assistant.audit import AuditEventType
from personal_ai_assistant.cli import EXIT_CONFIG, EXIT_INCOMPLETE, EXIT_OK, Session, main
from personal_ai_assistant.planning.contracts import Plan, ProposedAction
from personal_ai_assistant.planning.orchestrator import Orchestrator
from personal_ai_assistant.safety.confirmation import ConfirmationStore, PendingStatus
from personal_ai_assistant.safety.executor import ActionExecutor
from personal_ai_assistant.safety.gate import Policy
from personal_ai_assistant.tools.calendar import CreateEventTool, ListEventsTool
from tests.fakes.audit import MemoryAuditSink
from tests.fakes.calendar import FakeCalendarProvider
from tests.fakes.clock import FakeClock
from tests.fakes.planner import ScriptedPlanner

T0 = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
WINDOW = {"start": "2026-10-14T12:00:00+08:00", "end": "2026-10-14T18:00:00+08:00"}
DRAFT = {
    "title": "Dentist",
    "start": "2026-10-14T15:00:00+08:00",
    "end": "2026-10-14T16:00:00+08:00",
}
INVITE = DRAFT | {"attendees": ["guest@example.com"]}
SECRET_KEY = "sk-ant-api03-synthetic-test-key-000000000000"  # noqa: S105 - synthetic  gitleaks:allow

S = AuditEventType


class FakeConsole:
    def __init__(self, *answers: str | None, interactive: bool = True) -> None:
        self.answers = list(answers)
        self.interactive = interactive
        self.lines: list[str] = []
        self.prompts: list[str] = []

    def show(self, line: str) -> None:
        self.lines.append(line)

    def ask(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return self.answers.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def plan(*actions: tuple[str, dict[str, Any]], summary: str = "我會查詢") -> Plan:
    return Plan(
        summary=summary,
        actions=tuple(ProposedAction(tool=t, params=p, rationale="r") for t, p in actions),
        questions=(),
    )


class Env:
    def __init__(
        self, *script: Plan | BaseException, console: FakeConsole, policy: Policy | None = None
    ) -> None:
        self.clock = FakeClock(T0)
        self.audit = MemoryAuditSink()
        self.provider = FakeCalendarProvider(clock=self.clock)
        self.confirmations = ConfirmationStore(audit=self.audit, clock=self.clock)
        self.executor = ActionExecutor(
            [ListEventsTool(self.provider), CreateEventTool(self.provider)],
            policy=policy or Policy(writes_enabled=True),
            confirmations=self.confirmations,
            audit=self.audit,
            clock=self.clock,
        )
        self.console = console
        self.session = Session(
            orchestrator=Orchestrator(
                ScriptedPlanner(*script), self.executor, audit=self.audit, clock=self.clock
            ),
            executor=self.executor,
            confirmations=self.confirmations,
            console=console,
            approver="cli:tester",
        )


def test_read_runs_without_prompt_and_shows_data() -> None:
    env = Env(plan(("calendar.list_events", WINDOW)), console=FakeConsole())
    assert env.session.handle("明天下午？") == EXIT_OK
    assert env.console.prompts == []
    assert "✔ calendar.list_events：ok" in env.console.text
    assert "來源：fake_calendar" in env.console.text
    assert "未經驗證" in env.console.text


def test_yes_approves_exact_params_and_executes_once() -> None:
    env = Env(plan(("calendar.create_event", DRAFT)), console=FakeConsole("yes"))
    assert env.session.handle("約牙醫") == EXIT_OK
    assert len(env.provider.created) == 1
    assert env.provider.created[0].title == "Dentist"
    assert env.audit.types() == [
        S.PLAN_CREATED,
        S.ACTION_PENDING,
        S.CONFIRMATION_APPROVED,
        S.ACTION_STARTED,
        S.ACTION_EXECUTED,
    ]
    approved = env.audit.events[2]
    assert approved.actor == "cli:tester"
    assert "title: Dentist" in env.console.text


@pytest.mark.parametrize("answer", ["y", "YES ", "Yes", "ok", "", None])
def test_anything_but_yes_rejects(answer: str | None) -> None:
    env = Env(plan(("calendar.create_event", DRAFT)), console=FakeConsole(answer))
    assert env.session.handle("約牙醫") == EXIT_INCOMPLETE
    assert env.provider.created == []
    assert S.CONFIRMATION_REJECTED in env.audit.types()
    assert S.ACTION_STARTED not in env.audit.types()


def test_yes_with_surrounding_whitespace_is_accepted() -> None:
    env = Env(plan(("calendar.create_event", DRAFT)), console=FakeConsole("  yes\n"))
    assert env.session.handle("約牙醫") == EXIT_OK
    assert len(env.provider.created) == 1


def test_non_interactive_never_approves() -> None:
    env = Env(plan(("calendar.create_event", DRAFT)), console=FakeConsole(interactive=False))
    assert env.session.handle("約牙醫") == EXIT_INCOMPLETE
    assert env.console.prompts == []
    assert env.provider.created == []
    assert "非互動式終端" in env.console.text


def test_write_high_shows_banner_and_requires_yes() -> None:
    env = Env(plan(("calendar.create_event", INVITE)), console=FakeConsole("yes"))
    assert env.session.handle("邀請") == EXIT_OK
    assert "高風險" in env.console.text
    assert "WRITE_HIGH" in env.console.text
    assert 'attendees: ["guest@example.com"]' in env.console.text


def test_confirmation_escapes_bidi_and_control_characters() -> None:
    sneaky = DRAFT | {"title": "Pay\u202egnp.exe\u200b"}
    env = Env(
        plan(("calendar.create_event", sneaky), summary="ok\u202e\x1b[2J"),
        console=FakeConsole("no"),
    )
    env.session.handle("x")
    text = env.console.text
    assert "\u202e" not in text
    assert "\u200b" not in text
    assert "\x1b" not in text
    assert "title: Pay\\u202egnp.exe\\u200b" in text


def test_expired_pending_is_not_executed() -> None:
    console = FakeConsole("yes")
    env = Env(plan(("calendar.create_event", DRAFT)), console=console)
    original = console.ask

    def slow_human(prompt: str) -> str | None:
        env.clock.advance(timedelta(hours=1))
        return original(prompt)

    console.ask = slow_human  # type: ignore[method-assign]
    assert env.session.handle("約牙醫") == EXIT_INCOMPLETE
    assert env.provider.created == []
    assert "expired" in console.text


def test_approval_audit_failure_blocks_execution() -> None:
    env = Env(plan(("calendar.create_event", DRAFT)), console=FakeConsole("yes"))
    env.audit.fail_on = {S.CONFIRMATION_APPROVED}
    assert env.session.handle("約牙醫") == EXIT_INCOMPLETE
    assert env.provider.created == []
    assert "not executed" in env.console.text


def test_planning_failure_is_reported() -> None:
    env = Env(RuntimeError("boom"), console=FakeConsole())
    assert env.session.handle("x") == EXIT_INCOMPLETE
    assert "無法產生計畫" in env.console.text


def test_plan_without_actions_shows_questions() -> None:
    p = Plan(summary="需要更多資訊", actions=(), questions=("幾點？",))
    env = Env(p, console=FakeConsole())
    assert env.session.handle("約個時間") == EXIT_OK
    assert "需要你補充：幾點？" in env.console.text


def test_pending_store_rejects_after_decline() -> None:
    env = Env(plan(("calendar.create_event", DRAFT)), console=FakeConsole("no"))
    env.session.handle("x")
    pending_id = next(e.pending_id for e in env.audit.events if e.pending_id)
    assert env.confirmations.status(pending_id) is PendingStatus.REJECTED


# --- main(): configuration and end-to-end wiring --------------------------------


def base_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"ASSISTANT_AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl")} | extra


def test_main_missing_api_key_is_config_error(tmp_path: Path) -> None:
    console = FakeConsole()
    assert main(["hi"], env=base_env(tmp_path), console=console) == EXIT_CONFIG
    assert "ANTHROPIC_API_KEY is not set" in console.text


def test_main_invalid_settings_never_echo_key(tmp_path: Path) -> None:
    console = FakeConsole()
    env = base_env(tmp_path, ANTHROPIC_API_KEY=SECRET_KEY, ASSISTANT_ANTHROPIC_TIMEOUT_SECONDS="-1")
    assert main(["hi"], env=env, console=console) == EXIT_CONFIG
    assert SECRET_KEY not in console.text
    assert "timeout_seconds" in console.text


def test_main_bad_core_settings(tmp_path: Path) -> None:
    console = FakeConsole()
    env = base_env(tmp_path, ASSISTANT_TIMEZONE="Mars/Base")
    assert main(["hi"], env=env, console=console, planner=ScriptedPlanner()) == EXIT_CONFIG


def test_main_end_to_end_with_fake_planner(tmp_path: Path) -> None:
    console = FakeConsole()
    planner = ScriptedPlanner(plan(("calendar.list_events", WINDOW)))
    env = base_env(tmp_path, ASSISTANT_TIMEZONE="Asia/Taipei", ANTHROPIC_API_KEY=SECRET_KEY)
    code = main(["明天", "下午"], env=env, console=console, planner=planner, clock=FakeClock(T0))
    # Providers are not connected yet: the read is reported as unavailable, not empty.
    assert code == EXIT_INCOMPLETE
    assert "no provider configured" in console.text
    assert planner.requests[0].text == "明天 下午"
    assert planner.requests[0].now.isoformat() == "2026-10-13T09:00:00+08:00"
    log = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line)["event_type"] for line in log.splitlines()]
    assert events == ["plan.created", "action.started", "action.failed"]
    assert SECRET_KEY not in log
    assert "明天" not in log


def test_main_prompts_when_no_argument(tmp_path: Path) -> None:
    console = FakeConsole(None)
    assert main([], env=base_env(tmp_path), console=console, planner=ScriptedPlanner()) == EXIT_OK
    assert console.prompts == ["你想做什麼？ "]
