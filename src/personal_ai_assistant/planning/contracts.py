"""Provider-neutral planning contracts.

A ``Planner`` turns a user request into a ``Plan``: proposals only. A plan
carries no risk, approval or identity fields (``extra="forbid"``), so nothing a
model writes can raise its own permissions; risk and permission are decided by
``ActionExecutor``. LLM-backed planners talk to a vendor through the
``StructuredModel`` port, implemented by adapters in ``personal_ai_assistant.adapters``;
no vendor SDK type crosses this boundary.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from personal_ai_assistant.tools.base import ToolSpec

MAX_REQUEST_CHARS = 2000
MAX_ACTIONS = 5
MAX_QUESTIONS = 5

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class ProposedAction(BaseModel):
    """One tool call a planner suggests. ``params`` are checked later against the tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = Field(min_length=1, max_length=200)
    params: dict[str, Any]
    rationale: ShortText


class Plan(BaseModel):
    """Untrusted planner output, already schema-checked.

    ``questions`` lists information the planner needs but does not have; a
    planner must ask rather than invent a value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
    actions: tuple[ProposedAction, ...] = Field(max_length=MAX_ACTIONS)
    questions: tuple[ShortText, ...] = Field(max_length=MAX_QUESTIONS)


@dataclass(frozen=True, slots=True)
class PlanningRequest:
    """Built by the orchestrator; ``text`` is already length-checked."""

    text: str
    tools: tuple[ToolSpec, ...]
    # Local wall-clock time with offset, so relative dates ("tomorrow") resolve
    # against a known zone instead of a guessed one.
    now: datetime


class PlanningError(Exception):
    """Planning did not produce a usable plan. ``str(exc)`` is safe to show and audit."""


class ModelUnavailableError(PlanningError):
    """The model could not be reached or declined to answer (transport-level)."""


class InvalidPlanError(PlanningError):
    """The model answered, but the answer failed local validation."""


class Planner(Protocol):
    def plan(self, request: PlanningRequest) -> Plan:
        """Return a schema-valid plan or raise ``PlanningError``."""
        ...


@dataclass(frozen=True, slots=True)
class StructuredRequest:
    """A vendor-neutral request for one JSON document matching ``output_schema``."""

    system: str
    user: str
    output_schema: Mapping[str, Any]


class StructuredModel(Protocol):
    """Port implemented by LLM adapters.

    Returns the model's raw JSON text; the caller validates it. Adapters raise
    ``ModelUnavailableError`` for every vendor failure (network, auth, quota,
    refusal, truncation), with a message free of credentials and vendor types.
    """

    def generate(self, request: StructuredRequest) -> str: ...
