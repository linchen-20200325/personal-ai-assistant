from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from personal_ai_assistant.domain.calendar import EventDraft
from personal_ai_assistant.domain.result import ToolResult
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.tools.base import PlannedAction, Tool, ToolSpec
from personal_ai_assistant.tools.calendar import CreateEventTool, ListEventsTool
from tests.fakes.calendar import FakeCalendarProvider
from tests.fakes.clock import FakeClock

TPE = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 14, 9, 0, tzinfo=TPE)


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int


class Open(BaseModel):
    x: int


class ClosedWithOpenChild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    child: Open


class TestToolSpec:
    def test_accepts_closed_schema(self) -> None:
        ToolSpec(name="demo.read", description="d", base_risk=RiskLevel.READ, input_model=Closed)

    @pytest.mark.parametrize("model", [Open, ClosedWithOpenChild])
    def test_rejects_schema_that_allows_extra_fields(self, model: type[BaseModel]) -> None:
        with pytest.raises(ValueError, match="forbid extra fields"):
            ToolSpec(name="demo.read", description="d", base_risk=RiskLevel.READ, input_model=model)

    @pytest.mark.parametrize("name", ["read", "Demo.read", "demo.", "demo read", "demo.read\n"])
    def test_rejects_malformed_names(self, name: str) -> None:
        with pytest.raises(ValueError, match="invalid tool name"):
            ToolSpec(name=name, description="d", base_risk=RiskLevel.READ, input_model=Closed)

    def test_requires_description(self) -> None:
        with pytest.raises(ValueError, match="description"):
            ToolSpec(
                name="demo.read", description=" ", base_risk=RiskLevel.READ, input_model=Closed
            )


def test_default_assess_risk_is_base_risk() -> None:
    class Demo(Tool[Closed]):
        spec = ToolSpec(
            name="demo.write", description="d", base_risk=RiskLevel.WRITE_LOW, input_model=Closed
        )

        def run(self, params: Closed) -> ToolResult[Any]:
            raise NotImplementedError

    assert Demo().assess_risk(Closed(x=1)) is RiskLevel.WRITE_LOW


def test_planned_action_cannot_carry_risk_or_approval() -> None:
    for extra in ({"risk": 0}, {"approved": True}, {"pending_id": "p"}):
        with pytest.raises(ValidationError):
            PlannedAction.model_validate({"tool": "calendar.list_events", "params": {}} | extra)


class TestCalendarTools:
    def setup_method(self) -> None:
        self.provider = FakeCalendarProvider(clock=FakeClock(T0))

    def test_declared_risks(self) -> None:
        assert ListEventsTool.spec.base_risk is RiskLevel.READ
        assert CreateEventTool.spec.base_risk is RiskLevel.WRITE_LOW

    def test_inviting_attendees_escalates_to_write_high(self) -> None:
        tool = CreateEventTool(self.provider)
        solo = EventDraft(title="x", start=T0, end=T0 + timedelta(hours=1))
        invite = solo.model_copy(update={"attendees": ("guest@example.com",)})
        assert tool.assess_risk(solo) is RiskLevel.WRITE_LOW
        assert tool.assess_risk(invite) is RiskLevel.WRITE_HIGH
