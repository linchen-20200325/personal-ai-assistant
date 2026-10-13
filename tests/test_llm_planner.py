"""LLMPlanner: every model output is validated locally; nothing is guessed or repaired."""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from personal_ai_assistant.planning.contracts import (
    MAX_ACTIONS,
    InvalidPlanError,
    ModelUnavailableError,
    PlanningRequest,
)
from personal_ai_assistant.planning.llm_planner import (
    LLMPlanner,
    parse_plan,
    plan_output_schema,
)
from personal_ai_assistant.tools.calendar import CreateEventTool, ListEventsTool
from tests.fakes.calendar import FakeCalendarProvider
from tests.fakes.clock import FakeClock
from tests.fakes.planner import ScriptedModel

TAIPEI = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 13, 9, 30, tzinfo=TAIPEI)
WINDOW = {"start": "2026-10-14T12:00:00+08:00", "end": "2026-10-14T18:00:00+08:00"}


def wire(actions: list[dict[str, Any]] | None = None, **extra: Any) -> str:
    body: dict[str, Any] = {
        "summary": "查詢明天下午的行程",
        "actions": actions if actions is not None else [],
        "questions": [],
    }
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def act(tool: str = "calendar.list_events", params: Any = None, **extra: Any) -> dict[str, Any]:
    action = {
        "tool": tool,
        "arguments_json": json.dumps(WINDOW if params is None else params),
        "rationale": "need events",
    }
    action.update(extra)
    return action


def request() -> PlanningRequest:
    provider = FakeCalendarProvider(clock=FakeClock(NOW.astimezone(UTC)))
    specs = (ListEventsTool(provider).spec, CreateEventTool(provider).spec)
    return PlanningRequest(text="明天下午有什麼行程？", tools=specs, now=NOW)


def test_valid_output_becomes_plan() -> None:
    plan = parse_plan(wire([act()]))
    assert plan.actions[0].tool == "calendar.list_events"
    assert plan.actions[0].params == WINDOW
    assert plan.questions == ()


def test_questions_without_actions_is_valid() -> None:
    plan = parse_plan(wire([], questions=["幾點開始？"]))
    assert plan.actions == ()
    assert plan.questions == ("幾點開始？",)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not valid JSON"),
        ("", "not valid JSON"),
        ("[]", "failed schema"),
        ("null", "failed schema"),
        ('{"summary": "s", "actions": [], "questions": [], "summary": "t"}', "not valid JSON"),
        ('{"summary": "s", "actions": [], "questions": [NaN]}', "not valid JSON"),
        (wire(), None),
    ],
)
def test_malformed_output_is_rejected(text: str, message: str | None) -> None:
    if message is None:
        parse_plan(text)
        return
    with pytest.raises(InvalidPlanError, match=message):
        parse_plan(text)


@pytest.mark.parametrize("field", ["summary", "actions", "questions"])
def test_missing_field_is_not_defaulted(field: str) -> None:
    body = json.loads(wire())
    del body[field]
    with pytest.raises(InvalidPlanError, match=f"{field}: missing"):
        parse_plan(json.dumps(body))


@pytest.mark.parametrize(
    "smuggled",
    [
        {"risk": "READ"},
        {"approved": True},
        {"confirmed_by": "human:me"},
        {"auto_write_low": ["calendar.create_event"]},
    ],
)
def test_model_cannot_add_permission_fields(smuggled: dict[str, Any]) -> None:
    with pytest.raises(InvalidPlanError, match=r"<extra>|extra_forbidden"):
        parse_plan(wire(**smuggled))
    with pytest.raises(InvalidPlanError, match="extra_forbidden"):
        parse_plan(wire([act(**smuggled)]))


@pytest.mark.parametrize(
    ("arguments_json", "message"),
    [
        ("[1, 2]", "not a JSON object"),
        ('"text"', "not a JSON object"),
        ("{", "not valid JSON"),
        ('{"start": NaN}', "not valid JSON"),
        ('{"start": "a", "start": "b"}', "not valid JSON"),
    ],
)
def test_bad_arguments_json(arguments_json: str, message: str) -> None:
    action = act()
    action["arguments_json"] = arguments_json
    with pytest.raises(InvalidPlanError, match=message):
        parse_plan(wire([action]))


def test_wrong_types_are_not_coerced() -> None:
    action = act()
    action["arguments_json"] = {"start": "x"}  # object instead of string
    with pytest.raises(InvalidPlanError, match="string_type"):
        parse_plan(wire([action]))
    with pytest.raises(InvalidPlanError, match="string_type"):
        parse_plan(json.dumps({"summary": 1, "actions": [], "questions": []}))


def test_size_limits() -> None:
    with pytest.raises(InvalidPlanError, match="too_long"):
        parse_plan(wire([act()] * (MAX_ACTIONS + 1)))
    with pytest.raises(InvalidPlanError, match="too large"):
        parse_plan(" " * 70_000)


def test_error_messages_never_echo_model_text() -> None:
    secret = "sk-ant-synthetic-leak-0000"  # noqa: S105 - synthetic  gitleaks:allow
    with pytest.raises(InvalidPlanError) as info:
        parse_plan(json.dumps({"summary": "s", "actions": [], "questions": [], secret: secret}))
    assert secret not in str(info.value)
    with pytest.raises(InvalidPlanError) as info:
        parse_plan(f"oops {secret}")
    assert secret not in str(info.value)


def test_planner_sends_catalogue_time_and_closed_schema() -> None:
    model = ScriptedModel([wire([act()])])
    plan = LLMPlanner(model).plan(request())
    assert plan.actions[0].tool == "calendar.list_events"
    sent = model.requests[0]
    assert sent.user == "明天下午有什麼行程？"
    assert "2026-10-13T09:30+08:00" in sent.system
    assert "calendar.create_event" in sent.system
    assert "WRITE_LOW" in sent.system
    schema = sent.output_schema
    assert schema["properties"]["actions"]["items"]["properties"]["tool"]["enum"] == [
        "calendar.create_event",
        "calendar.list_events",
    ]


def _objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_objects(item))
    return found


def test_output_schema_is_closed_everywhere() -> None:
    objects = _objects(plan_output_schema(["a.b"]))
    assert objects
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_model_errors_propagate_and_non_text_is_rejected() -> None:
    with pytest.raises(ModelUnavailableError):
        LLMPlanner(ScriptedModel([ModelUnavailableError("down")])).plan(request())
    with pytest.raises(InvalidPlanError, match="non-text"):
        LLMPlanner(ScriptedModel([{"summary": "s"}])).plan(request())


def test_overflowing_float_and_deep_nesting_are_rejected() -> None:
    action = act()
    action["arguments_json"] = '{"limit": 1e400}'
    with pytest.raises(InvalidPlanError, match="not valid JSON"):
        parse_plan(wire([action]))
    with pytest.raises(InvalidPlanError, match="not valid JSON"):
        parse_plan("[" * 30_000 + "]" * 30_000)
