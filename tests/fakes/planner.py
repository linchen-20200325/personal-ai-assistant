"""Deterministic planners and model ports for tests. No network, no randomness."""

from collections.abc import Iterable

from personal_ai_assistant.planning.contracts import (
    Plan,
    PlanningRequest,
    StructuredRequest,
)


class ScriptedPlanner:
    """Returns (or raises) the scripted items in order; records every request."""

    def __init__(self, *script: Plan | BaseException) -> None:
        self._script = list(script)
        self.requests: list[PlanningRequest] = []

    def plan(self, request: PlanningRequest) -> Plan:
        self.requests.append(request)
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class ScriptedModel:
    """``StructuredModel`` returning canned raw text (or raising); records requests."""

    def __init__(self, outputs: Iterable[object | BaseException]) -> None:
        self._outputs = list(outputs)
        self.requests: list[StructuredRequest] = []

    def generate(self, request: StructuredRequest) -> str:
        self.requests.append(request)
        item = self._outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[return-value]  # deliberately untyped to probe validation
