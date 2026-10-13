"""B2 tools: declared risks, input validation and executor integration."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from personal_ai_assistant.audit import REDACTED, AuditEventType
from personal_ai_assistant.domain.document import DocumentContent, ReadDocument
from personal_ai_assistant.domain.email import EmailDraft, EmailMessage, EmailSearch
from personal_ai_assistant.domain.note import Note, NoteDraft
from personal_ai_assistant.domain.reminder import Reminder, ReminderDraft
from personal_ai_assistant.domain.result import DataStatus, Page
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.safety.confirmation import ConfirmationStore
from personal_ai_assistant.safety.executor import ActionExecutor, Outcome
from personal_ai_assistant.safety.gate import Policy
from personal_ai_assistant.tools.base import PlannedAction, Tool
from personal_ai_assistant.tools.document import ReadDocumentTool, SearchDocumentsTool
from personal_ai_assistant.tools.email import CreateEmailDraftTool, GetEmailTool, SearchEmailTool
from personal_ai_assistant.tools.note import CreateNoteTool, SearchNotesTool
from personal_ai_assistant.tools.reminder import CreateReminderTool, ListRemindersTool
from tests.fakes.audit import MemoryAuditSink
from tests.fakes.clock import FakeClock
from tests.fakes.document import FakeDocumentProvider
from tests.fakes.email import FakeEmailProvider
from tests.fakes.note import FakeNoteProvider
from tests.fakes.reminder import FakeReminderProvider

T0 = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
HUMAN = "human:test"


def test_declared_risks() -> None:
    expected: dict[type[Tool[Any]], RiskLevel] = {
        SearchEmailTool: RiskLevel.READ,
        GetEmailTool: RiskLevel.READ,
        CreateEmailDraftTool: RiskLevel.DRAFT,
        SearchDocumentsTool: RiskLevel.READ,
        ReadDocumentTool: RiskLevel.READ,
        SearchNotesTool: RiskLevel.READ,
        CreateNoteTool: RiskLevel.WRITE_LOW,
        ListRemindersTool: RiskLevel.READ,
        CreateReminderTool: RiskLevel.WRITE_LOW,
    }
    assert {tool: tool.spec.base_risk for tool in expected} == expected


class TestValidation:
    def test_draft_needs_a_valid_recipient(self) -> None:
        for to in ((), ("not-an-address",)):
            with pytest.raises(ValidationError):
                EmailDraft(to=to, subject="s", body="b")

    @pytest.mark.parametrize("limit", [0, 101])
    def test_search_limit_is_bounded(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            EmailSearch(limit=limit)

    def test_blank_search_text_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmailSearch(text="   ")

    @pytest.mark.parametrize("max_chars", [0, 200_001])
    def test_read_size_is_bounded(self, max_chars: int) -> None:
        with pytest.raises(ValidationError):
            ReadDocument(doc_id="d", max_chars=max_chars)

    @pytest.mark.parametrize("doc_id", ["", "has space", "x" * 513])
    def test_identifiers_are_opaque_tokens(self, doc_id: str) -> None:
        with pytest.raises(ValidationError):
            ReadDocument(doc_id=doc_id)

    def test_completeness_flags_must_be_stated(self) -> None:
        with pytest.raises(ValidationError):
            Page[int].model_validate({"items": ()})
        with pytest.raises(ValidationError):
            DocumentContent.model_validate(
                {"doc_id": "d", "title": None, "mime_type": None, "text": "t"}
            )

    @pytest.mark.parametrize(
        "address",
        [
            "friend<attacker@evil.example>",
            "x,second@evil.example",
            "alice)@example.com",
            '"quoted"@example.com',
            "a@b",
            "a@exa mple.com",
            "a;b@example.com",
        ],
    )
    def test_recipient_must_be_a_single_bare_address(self, address: str) -> None:
        with pytest.raises(ValidationError):
            EmailDraft(to=(address,), subject="s", body="b")

    @pytest.mark.parametrize("value", ["Dinner\r\nBcc: spy@example.com", "a\x00b", "tab\there"])
    def test_single_line_fields_reject_control_characters(self, value: str) -> None:
        with pytest.raises(ValidationError):
            EmailDraft(to=("a@example.com",), subject=value, body="b")
        with pytest.raises(ValidationError):
            NoteDraft(title=value, body="b")
        with pytest.raises(ValidationError):
            ReminderDraft(text=value, due_at=T0)

    @pytest.mark.parametrize("value", [True, "5", 5.0])
    def test_integer_inputs_are_not_coerced(self, value: object) -> None:
        with pytest.raises(ValidationError):
            EmailSearch.model_validate({"limit": value})
        with pytest.raises(ValidationError):
            ReadDocument.model_validate({"doc_id": "d", "max_chars": value})

    def test_reminder_text_cannot_be_blank_and_note_body_may_be_unknown(self) -> None:
        with pytest.raises(ValidationError):
            Reminder(reminder_id="r", text="", due_at=T0, created_at=None)
        assert Note(note_id="n", title=None, body=None, created_at=None).body is None

    def test_email_message_has_no_silent_defaults(self) -> None:
        with pytest.raises(ValidationError) as exc:
            EmailMessage.model_validate({"message_id": "m"})
        assert len(exc.value.errors()) == len(EmailMessage.model_fields) - 1


class TestReminderTool:
    def setup_method(self) -> None:
        self.clock = FakeClock(T0)
        self.provider = FakeReminderProvider(clock=self.clock)
        self.tool = CreateReminderTool(self.provider, clock=self.clock)

    @pytest.mark.parametrize("offset", [timedelta(0), timedelta(minutes=-5)])
    def test_due_time_must_be_in_the_future(self, offset: timedelta) -> None:
        result = self.tool.run(ReminderDraft(text="x", due_at=T0 + offset))
        assert result.status is DataStatus.ERROR
        assert self.provider.reminders == []

    def test_future_reminder_is_created(self) -> None:
        result = self.tool.run(ReminderDraft(text="x", due_at=T0 + timedelta(minutes=1)))
        assert result.succeeded


# --- executor integration ----------------------------------------------------


class Env:
    def __init__(self, *, writes: bool = True) -> None:
        self.clock = FakeClock(T0)
        self.audit = MemoryAuditSink()
        self.store = ConfirmationStore(audit=self.audit, clock=self.clock)
        self.email = FakeEmailProvider(clock=self.clock)
        self.docs = FakeDocumentProvider(clock=self.clock)
        self.notes = FakeNoteProvider(clock=self.clock)
        self.reminders = FakeReminderProvider(clock=self.clock)
        tools: list[Tool[Any]] = [
            SearchEmailTool(self.email),
            GetEmailTool(self.email),
            CreateEmailDraftTool(self.email),
            SearchDocumentsTool(self.docs),
            ReadDocumentTool(self.docs),
            CreateNoteTool(self.notes),
            SearchNotesTool(self.notes),
            CreateReminderTool(self.reminders, clock=self.clock),
            ListRemindersTool(self.reminders),
        ]
        self.executor = ActionExecutor(
            tools,
            policy=Policy(writes_enabled=writes),
            confirmations=self.store,
            audit=self.audit,
            clock=self.clock,
        )

    def submit(self, tool: str, params: dict[str, Any]) -> Any:
        return self.executor.submit(PlannedAction(tool=tool, params=params))


DRAFT = {"to": ["friend@example.com"], "subject": "Dinner", "body": "Private message text"}


def test_email_draft_runs_without_confirmation_and_audit_hides_body() -> None:
    env = Env(writes=False)  # DRAFT is not a write
    result = env.submit("email.create_draft", DRAFT)
    assert result.outcome is Outcome.EXECUTED
    assert len(env.email.drafts) == 1
    params = env.audit.events[0].params
    assert params is not None
    assert params["body"] == REDACTED
    assert params["to"] == ["f***@example.com"]


def test_audit_masks_subject_and_reminder_text() -> None:
    env = Env()
    env.submit("email.create_draft", DRAFT | {"subject": "Private matter"})
    env.submit("reminders.create", {"text": "Take medicine", "due_at": "2026-10-15T01:00:00Z"})
    dumped = str([e.params for e in env.audit.events])
    assert "Private matter" not in dumped
    assert "Take medicine" not in dumped


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        ("notes.create", {"body": "Remember the passport"}),
        ("reminders.create", {"text": "Dentist", "due_at": "2026-10-15T15:00:00+08:00"}),
    ],
)
def test_writes_require_confirmation_by_default(tool: str, params: dict[str, Any]) -> None:
    env = Env()
    result = env.submit(tool, params)
    assert result.outcome is Outcome.PENDING_CONFIRMATION
    assert env.notes.notes == []
    assert env.reminders.reminders == []


def test_reminder_that_became_past_due_before_approval_is_not_created() -> None:
    env = Env()
    pending = env.submit("reminders.create", {"text": "x", "due_at": "2026-10-13T01:05:00+00:00"})
    env.store.approve(pending.pending.pending_id, digest=pending.pending.digest, approver=HUMAN)
    env.clock.advance(timedelta(minutes=6))
    result = env.executor.execute_approved(pending.pending.pending_id)
    assert result.outcome is Outcome.FAILED
    assert env.reminders.reminders == []
    assert env.audit.types()[-1] is AuditEventType.ACTION_FAILED


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        (
            "email.search",
            {"window": {"start": "2026-10-13T00:00:00", "end": "2026-10-14T00:00:00"}},
        ),
        (
            "email.search",
            {
                "window": {
                    "start": "2026-10-13T00:00:00Z",
                    "end": "2026-10-14T00:00:00Z",
                    "tz": "x",
                }
            },
        ),
        ("email.create_draft", DRAFT | {"send_now": True}),
        ("documents.read", {"doc_id": "d", "max_chars": 10**9}),
    ],
)
def test_invalid_inputs_are_denied(tool: str, params: dict[str, Any]) -> None:
    env = Env()
    assert env.submit(tool, params).outcome is Outcome.DENIED
    assert env.email.drafts == []


def test_provider_outage_is_a_failure_not_an_empty_inbox() -> None:
    env = Env()
    env.email.outage = "upstream timeout"
    result = env.submit("email.search", {})
    assert result.outcome is Outcome.FAILED
    assert result.result.data is None


def test_email_tools_cannot_send() -> None:
    env = Env()
    names = {spec.name for spec in env.executor.specs()}
    assert not any("send" in name for name in names)


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        ("email.search", {"text": "dinner", "limit": 5}),
        ("documents.search", {"text": "trip"}),
        ("notes.search", {}),
        ("reminders.list", {"start": "2026-10-13T00:00:00Z", "end": "2026-10-20T00:00:00Z"}),
    ],
)
def test_read_tools_execute_through_executor_with_provenance(
    tool: str, params: dict[str, Any]
) -> None:
    env = Env(writes=False)
    result = env.submit(tool, params)
    assert result.outcome is Outcome.EXECUTED
    assert result.result.status is DataStatus.OK
    assert result.result.provenance is not None


@pytest.mark.parametrize(
    ("tool", "params"),
    [("email.get", {"message_id": "nope"}), ("documents.read", {"doc_id": "nope"})],
)
def test_lookup_of_unknown_item_is_a_failure(tool: str, params: dict[str, Any]) -> None:
    result = Env().submit(tool, params)
    assert result.outcome is Outcome.FAILED
    assert result.result.status is DataStatus.UNAVAILABLE


def test_approved_note_is_created() -> None:
    env = Env()
    pending = env.submit("notes.create", {"title": "Trip", "body": "Book ryokan"})
    env.store.approve(pending.pending.pending_id, digest=pending.pending.digest, approver=HUMAN)
    assert env.executor.execute_approved(pending.pending.pending_id).outcome is Outcome.EXECUTED
    assert [n.body for n in env.notes.notes] == ["Book ryokan"]
