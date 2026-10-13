"""Planner backed by any ``StructuredModel`` (LLM adapter).

The model sees the tool catalogue (names, descriptions, declared risk, input
schemas) and the user's request, and must answer with one JSON document
matching ``plan_output_schema``. Everything it returns is validated locally:
strict JSON (no NaN / Infinity, no duplicate keys), a closed schema, and size
limits. Nothing is repaired, defaulted or guessed: any deviation rejects the
whole plan.

Tool arguments travel as a JSON-encoded string (``arguments_json``) so the
output schema stays closed (``additionalProperties: false`` everywhere), which
vendor structured-output modes require; the arguments are decoded here and
checked against the tool's own input model by the orchestrator and again by
``ActionExecutor``.
"""

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from personal_ai_assistant.planning.contracts import (
    MAX_ACTIONS,
    MAX_QUESTIONS,
    InvalidPlanError,
    Plan,
    PlanningRequest,
    StructuredModel,
    StructuredRequest,
)
from personal_ai_assistant.tools.base import ToolSpec
from personal_ai_assistant.validation import validation_summary

MAX_OUTPUT_CHARS = 64_000

SYSTEM_PROMPT = """\
You are the planning component of a personal assistant. You do not execute anything.
You propose tool calls; separate code validates them, classifies their risk and asks
the human to confirm any write. You cannot grant, skip or change confirmation.

Rules:
- Use only the tools listed below, with arguments that match their input schema.
- Never invent facts or values. If a required value (date, time, recipient, title,
  identifier, ...) is not stated or cannot be derived with certainty from the request
  and the current time, do not propose that action; ask in "questions" instead.
- Datetimes are ISO-8601 with an explicit UTC offset. Resolve relative dates
  ("tomorrow afternoon") against the current local time given below; if the exact
  time is ambiguous, ask instead of picking one.
- Prefer read-only tools to gather facts. Propose writes only when the user asked
  for them. There is no tool to send e-mail, pay, book or delete; do not pretend
  otherwise and do not describe such an action as done.
- "summary" briefly tells the user, in the language of their request, what you
  propose. It is shown as unverified planner text, never as the confirmation.
- "arguments_json" is a JSON object encoded as a string.
- At most {max_actions} actions and {max_questions} questions.

Current local time: {now}

Available tools (JSON):
{catalogue}
"""


def tool_catalogue(tools: tuple[ToolSpec, ...]) -> str:
    entries = [
        {
            "name": spec.name,
            "description": spec.description,
            "declared_risk": spec.base_risk.name,
            "input_schema": spec.input_model.model_json_schema(),
        }
        for spec in sorted(tools, key=lambda s: s.name)
    ]
    return json.dumps(entries, sort_keys=True, ensure_ascii=False, indent=1)


def build_system_prompt(tools: tuple[ToolSpec, ...], now: datetime) -> str:
    return SYSTEM_PROMPT.format(
        max_actions=MAX_ACTIONS,
        max_questions=MAX_QUESTIONS,
        now=now.isoformat(timespec="minutes"),
        catalogue=tool_catalogue(tools),
    )


def plan_output_schema(tool_names: list[str]) -> dict[str, Any]:
    """Closed JSON Schema the model's answer must match (vendor-neutral)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "actions", "questions"],
        "properties": {
            "summary": {"type": "string"},
            "questions": {"type": "array", "items": {"type": "string"}},
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tool", "arguments_json", "rationale"],
                    "properties": {
                        "tool": {"type": "string", "enum": sorted(tool_names)},
                        "arguments_json": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                },
            },
        },
    }


class _WireAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tool: str
    arguments_json: str = Field(max_length=16_000)
    rationale: str


class _WirePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str
    actions: list[_WireAction]
    questions: list[str]


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not allowed")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def strict_json_loads(text: str) -> Any:
    """``json.loads`` without NaN / Infinity and without silently merged duplicate keys."""
    return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicate_keys)


def parse_plan(text: str) -> Plan:
    """Validate raw model output into a ``Plan``; raise ``InvalidPlanError`` otherwise.

    Error messages carry positions and error types only, never model text.
    """
    if len(text) > MAX_OUTPUT_CHARS:
        raise InvalidPlanError("model output too large")
    try:
        raw = strict_json_loads(text)
    except ValueError:  # JSONDecodeError is a ValueError
        raise InvalidPlanError("model output is not valid JSON") from None
    try:
        wire = _WirePlan.model_validate(raw)
    except ValidationError as exc:
        raise InvalidPlanError(f"model output failed schema: {validation_summary(exc)}") from None
    actions: list[dict[str, Any]] = []
    for index, action in enumerate(wire.actions):
        try:
            params = strict_json_loads(action.arguments_json)
        except ValueError:
            raise InvalidPlanError(f"actions.{index}.arguments_json is not valid JSON") from None
        if not isinstance(params, dict):
            raise InvalidPlanError(f"actions.{index}.arguments_json is not a JSON object")
        actions.append({"tool": action.tool, "params": params, "rationale": action.rationale})
    try:
        return Plan.model_validate(
            {"summary": wire.summary, "actions": actions, "questions": wire.questions}
        )
    except ValidationError as exc:
        raise InvalidPlanError(f"plan failed schema: {validation_summary(exc)}") from None


class LLMPlanner:
    """``Planner`` implementation over a vendor-neutral ``StructuredModel``."""

    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    def plan(self, request: PlanningRequest) -> Plan:
        structured = StructuredRequest(
            system=build_system_prompt(request.tools, request.now),
            user=request.text,
            output_schema=plan_output_schema([spec.name for spec in request.tools]),
        )
        text = self._model.generate(structured)
        if not isinstance(text, str):
            raise InvalidPlanError("model adapter returned a non-text value")
        return parse_plan(text)
