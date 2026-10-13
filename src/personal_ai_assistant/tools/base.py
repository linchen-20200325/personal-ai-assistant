"""Tool contract.

A tool binds a name, a strict input schema and a declared base risk to a
provider call. Planners never hold tool instances: they submit
``PlannedAction`` data to ``ActionExecutor``, which is the only component that
invokes ``Tool.run`` (see ``safety/executor.py``).
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_assistant.domain.result import ToolResult
from personal_ai_assistant.domain.risk import RiskLevel

TOOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")


def _open_objects(schema: Any) -> list[str]:
    """Titles of object schemas in ``schema`` that accept undeclared properties."""
    found: list[str] = []
    if isinstance(schema, dict):
        if "properties" in schema and schema.get("additionalProperties") is not False:
            found.append(str(schema.get("title", "<anonymous>")))
        for value in schema.values():
            found.extend(_open_objects(value))
    elif isinstance(schema, list):
        for item in schema:
            found.extend(_open_objects(item))
    return found


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    base_risk: RiskLevel
    input_model: type[BaseModel]

    def __post_init__(self) -> None:
        if not TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError(f"invalid tool name {self.name!r}; expected 'domain.action'")
        if not self.description.strip():
            raise ValueError(f"tool {self.name!r} needs a description")
        open_objects = _open_objects(self.input_model.model_json_schema())
        if open_objects:
            raise ValueError(
                f"tool {self.name!r} input schema must forbid extra fields: {open_objects}"
            )


class Tool[P: BaseModel](ABC):
    spec: ToolSpec

    def assess_risk(self, params: P) -> RiskLevel:
        """Parameter-dependent risk.

        Can only raise risk: the executor applies
        ``max(spec.base_risk, assess_risk(params))``.
        """
        return self.spec.base_risk

    @abstractmethod
    def run(self, params: P) -> ToolResult[Any]:
        """Perform the action. Only ``ActionExecutor`` may call this."""


class PlannedAction(BaseModel):
    """Untrusted proposal from a planner / LLM.

    Deliberately carries no risk, approval or identity fields; those are
    decided by code, never by the proposer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)
