"""Command-line human channel and composition root.

``pai "明天下午有什麼行程？"`` plans with the configured LLM, shows the plan,
runs what the gate allows, and asks the human to confirm anything else.

This module is the only place that wires an LLM adapter into the core and the
only caller of ``ConfirmationStore.approve`` (enforced by an architecture
test). The confirmation screen is rendered by code from
``PendingAction.params_json`` (exactly what the digest covers), never from
planner text, and approval requires typing ``yes`` on an interactive terminal.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from personal_ai_assistant.audit import JsonlAuditSink
from personal_ai_assistant.clock import Clock, system_clock
from personal_ai_assistant.config import Settings, load_settings
from personal_ai_assistant.display import (
    escape_display,
    render_data,
    render_params,
    render_params_json,
)
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.planning.contracts import Planner
from personal_ai_assistant.planning.llm_planner import LLMPlanner
from personal_ai_assistant.planning.orchestrator import Orchestrator, Proposal
from personal_ai_assistant.providers.calendar import CalendarProvider
from personal_ai_assistant.providers.unconfigured import UnconfiguredProvider
from personal_ai_assistant.safety.confirmation import (
    ConfirmationError,
    ConfirmationStore,
    PendingAction,
)
from personal_ai_assistant.safety.executor import ActionExecutor, ExecutionResult, Outcome
from personal_ai_assistant.tools.base import Tool
from personal_ai_assistant.tools.calendar import CreateEventTool, ListEventsTool
from personal_ai_assistant.tools.document import ReadDocumentTool, SearchDocumentsTool
from personal_ai_assistant.tools.email import CreateEmailDraftTool, GetEmailTool, SearchEmailTool
from personal_ai_assistant.tools.note import CreateNoteTool, SearchNotesTool
from personal_ai_assistant.tools.reminder import CreateReminderTool, ListRemindersTool

CONFIRM_WORD = "yes"
DATA_DISPLAY_LIMIT = 4000

EXIT_OK = 0
EXIT_INCOMPLETE = 1  # planning failed, or an action was denied / failed / not confirmed
EXIT_CONFIG = 2


class Console(Protocol):
    @property
    def interactive(self) -> bool:
        """True only when a human is at a terminal (stdin and stdout are TTYs)."""
        ...

    def show(self, line: str) -> None: ...

    def ask(self, prompt: str) -> str | None:
        """One line of input, or None on EOF / interrupt."""
        ...


class TerminalConsole:
    @property
    def interactive(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    def show(self, line: str) -> None:
        print(line)

    def ask(self, prompt: str) -> str | None:
        _discard_typeahead()
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return None


def _discard_typeahead() -> None:
    """Drop keystrokes typed before the prompt appeared (e.g. 'yes' typed while planning)."""
    if not sys.stdin.isatty():
        return
    try:
        import termios

        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (ImportError, OSError):  # non-POSIX terminal: nothing to flush
        pass


def build_tools(
    provider: UnconfiguredProvider,
    *,
    clock: Clock = system_clock,
    calendar: CalendarProvider | None = None,
    calendar_read_only: bool = False,
) -> list[Tool[Any]]:
    """Tool registry. A read-only calendar does not offer ``calendar.create_event``."""
    calendar_provider: CalendarProvider = provider if calendar is None else calendar
    tools: list[Tool[Any]] = [ListEventsTool(calendar_provider)]
    if not calendar_read_only:
        tools.append(CreateEventTool(calendar_provider))
    return [
        *tools,
        SearchEmailTool(provider),
        GetEmailTool(provider),
        CreateEmailDraftTool(provider),
        SearchDocumentsTool(provider),
        ReadDocumentTool(provider),
        SearchNotesTool(provider),
        CreateNoteTool(provider),
        ListRemindersTool(provider),
        CreateReminderTool(provider, clock=clock),
    ]


class Session:
    """One request through plan -> gate -> confirm -> execute, shown on a console."""

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        executor: ActionExecutor,
        confirmations: ConfirmationStore,
        console: Console,
        approver: str,
    ) -> None:
        self._orchestrator = orchestrator
        self._executor = executor
        self._confirmations = confirmations
        self._console = console
        self._approver = approver

    def handle(self, text: str) -> int:
        proposal = self._orchestrator.propose(text)
        if proposal.plan is None:
            self._show(f"無法產生計畫：{escape_display(proposal.error or 'unknown error')}")
            return EXIT_INCOMPLETE
        self._show_plan(proposal)
        if not proposal.plan.actions:
            self._show("沒有可執行的動作。")
            return EXIT_OK
        complete = True
        for result in self._orchestrator.execute(proposal):
            if result.outcome is Outcome.PENDING_CONFIRMATION and result.pending is not None:
                result = self._confirm(result.pending)
            complete = self._show_result(result) and complete
        return EXIT_OK if complete else EXIT_INCOMPLETE

    def _show(self, line: str) -> None:
        self._console.show(line)

    def _show_plan(self, proposal: Proposal) -> None:
        plan = proposal.plan
        if plan is None:
            return
        self._show(f"計畫 {proposal.plan_id}")
        self._show(f"Planner 說明（未經驗證，僅供參考）：{escape_display(plan.summary)}")
        for question in plan.questions:
            self._show(f"  需要你補充：{escape_display(question)}")
        for index, action in enumerate(plan.actions, start=1):
            self._show(f"[{index}] {escape_display(action.tool)}")
            for line in render_params(action.params):
                self._show(line)

    def _confirm(self, pending: PendingAction) -> ExecutionResult:
        self._show("")
        self._show("=== 需要人工確認 ===")
        if pending.risk is RiskLevel.WRITE_HIGH:
            self._show("!!! 高風險：對外或不可逆的動作 !!!")
        self._show(f"動作：{escape_display(pending.tool)}（風險 {pending.risk.name}）")
        self._show(f"有效期限：{pending.expires_at.isoformat(timespec='seconds')}")
        self._show("參數（將以此內容執行）：")
        for line in render_params_json(pending.params_json):
            self._show(line)
        if not self._console.interactive:
            self._show("非互動式終端，無法取得人工確認；已取消。")
            return self._reject(pending, "not confirmed: no interactive terminal")
        answer = self._console.ask(f"輸入 {CONFIRM_WORD} 確認執行，其他任何輸入皆取消：")
        if answer is None or answer.strip() != CONFIRM_WORD:
            return self._reject(pending, "not confirmed by user")
        try:
            self._confirmations.approve(
                pending.pending_id, digest=pending.digest, approver=self._approver
            )
        except ConfirmationError as exc:
            return ExecutionResult(
                pending.action_id,
                Outcome.DENIED,
                pending.tool,
                pending.risk,
                reason=f"approval failed: {exc}",
            )
        except Exception:
            return ExecutionResult(
                pending.action_id,
                Outcome.DENIED,
                pending.tool,
                pending.risk,
                reason="approval could not be recorded; not executed",
            )
        return self._executor.execute_approved(pending.pending_id)

    def _reject(self, pending: PendingAction, reason: str) -> ExecutionResult:
        try:
            self._confirmations.reject(pending.pending_id, approver=self._approver)
        except Exception:
            # Unapproved pending actions expire on their own; never block on this.
            reason = f"{reason}; rejection not recorded"
        return ExecutionResult(
            pending.action_id, Outcome.DENIED, pending.tool, pending.risk, reason=reason
        )

    def _show_result(self, result: ExecutionResult) -> bool:
        tool = escape_display(result.tool or "?")
        if result.outcome is Outcome.EXECUTED and result.result is not None:
            tool_result = result.result
            self._show(f"✔ {tool}：{tool_result.status.value}")
            if tool_result.missing:
                self._show(f"  缺漏欄位：{escape_display(', '.join(tool_result.missing))}")
            if tool_result.provenance is not None:
                self._show(f"  來源：{escape_display(tool_result.provenance.source)}")
            dumped = tool_result.model_dump(mode="json")["data"]
            for line in render_data(dumped, limit=DATA_DISPLAY_LIMIT):
                self._show(f"  {line}")
            if result.reason:
                self._show(f"  注意：{escape_display(result.reason)}")
            return True
        label = {Outcome.DENIED: "已拒絕", Outcome.FAILED: "失敗"}.get(result.outcome, "未完成")
        self._show(f"✘ {tool}：{label}：{escape_display(result.reason or '')}")
        return False


def _approver_identity() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    return f"cli:{user}"


def _local_time_source(clock: Clock, timezone: str | None) -> Clock:
    if timezone is None:
        return lambda: clock().astimezone()
    zone = ZoneInfo(timezone)
    return lambda: clock().astimezone(zone)


def _build_planner(env: Mapping[str, str]) -> Planner:
    # Imported lazily: the SDK is an optional extra and the core must run without it.
    from personal_ai_assistant.adapters.anthropic_model import (
        AnthropicSettings,
        AnthropicStructuredModel,
    )

    return LLMPlanner(AnthropicStructuredModel.from_settings(AnthropicSettings.from_env(env)))


def _build_google_calendar(settings: Settings, clock: Clock) -> CalendarProvider:
    # Imported lazily: the Google libraries are an optional extra.
    from personal_ai_assistant.adapters.google_auth import (
        KeyringTokenStore,
        calendar_service_factory,
    )
    from personal_ai_assistant.adapters.google_calendar import GoogleCalendarProvider

    return GoogleCalendarProvider(
        calendar_service_factory(KeyringTokenStore()),
        clock=clock,
        calendar_id=settings.google_calendar_id,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    console: Console | None = None,
    planner: Planner | None = None,
    calendar: CalendarProvider | None = None,
    clock: Clock = system_clock,
) -> int:
    parser = argparse.ArgumentParser(
        prog="pai", description="Personal AI assistant (plan, confirm, execute)."
    )
    parser.add_argument("request", nargs="*", help="what you want done, in natural language")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    console = console or TerminalConsole()

    try:
        settings = load_settings(env)
    except (ValidationError, ValueError) as exc:
        console.show(
            f"設定錯誤：{escape_display(type(exc).__name__)}（請檢查 ASSISTANT_* 環境變數）"
        )
        return EXIT_CONFIG
    if planner is None:
        try:
            planner = _build_planner(env)
        except ImportError:
            console.show("缺少 Anthropic SDK：請安裝 extra `personal-ai-assistant[anthropic]`。")
            return EXIT_CONFIG
        except Exception as exc:
            # AnthropicConfigError messages never contain the key.
            console.show(f"LLM 設定錯誤：{escape_display(str(exc))}")
            return EXIT_CONFIG

    calendar_read_only = settings.calendar_provider == "google"
    if calendar is None and calendar_read_only:
        try:
            calendar = _build_google_calendar(settings, clock)
        except ImportError:
            console.show("缺少 Google 套件：請安裝 extra `personal-ai-assistant[google]`。")
            return EXIT_CONFIG

    text = " ".join(args.request).strip()
    if not text:
        answer = console.ask("你想做什麼？ ")
        text = (answer or "").strip()
        if not text:
            return EXIT_OK

    audit = JsonlAuditSink(settings.audit_log_path)
    confirmations = ConfirmationStore(audit=audit, clock=clock)
    try:
        executor = ActionExecutor(
            build_tools(
                UnconfiguredProvider(),
                clock=clock,
                calendar=calendar,
                calendar_read_only=calendar_read_only,
            ),
            policy=settings.policy,
            confirmations=confirmations,
            audit=audit,
            clock=clock,
        )
    except ValueError as exc:  # e.g. ASSISTANT_AUTO_WRITE_LOW names a non-WRITE_LOW tool
        console.show(f"設定錯誤：{escape_display(str(exc))}")
        return EXIT_CONFIG
    orchestrator = Orchestrator(
        planner,
        executor,
        audit=audit,
        clock=clock,
        local_time=_local_time_source(clock, settings.timezone),
    )
    session = Session(
        orchestrator=orchestrator,
        executor=executor,
        confirmations=confirmations,
        console=console,
        approver=_approver_identity(),
    )
    try:
        return session.handle(text)
    except Exception as exc:
        # Results already shown stay valid; no pending action is approved by this path.
        console.show(f"處理中斷：{escape_display(type(exc).__name__)}；請查看 audit log 後再重試。")
        return EXIT_INCOMPLETE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
