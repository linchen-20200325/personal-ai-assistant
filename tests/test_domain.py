"""Domain model invariants: unknown stays unknown, naive datetimes rejected."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from personal_ai_assistant.domain.calendar import CalendarEvent, EventDraft, TimeRange
from personal_ai_assistant.domain.result import DataStatus, Provenance, ToolResult
from personal_ai_assistant.domain.risk import RiskLevel

TPE = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=TPE)
PROV = Provenance(source="fake", retrieved_at=NOW, source_ref=None)


def test_risk_levels_are_totally_ordered() -> None:
    assert RiskLevel.READ < RiskLevel.DRAFT < RiskLevel.WRITE_LOW < RiskLevel.WRITE_HIGH
    assert [r.is_write for r in RiskLevel] == [False, False, True, True]


class TestToolResult:
    def test_empty_success_is_distinct_from_failure(self) -> None:
        empty = ToolResult[list[int]].ok([], PROV)
        failure = ToolResult[list[int]].failed("timeout")
        assert empty.succeeded
        assert empty.data == []
        assert not failure.succeeded
        assert failure.data is None
        assert failure.status is DataStatus.ERROR

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"status": "ok", "data": None, "provenance": PROV}, id="ok-without-data"),
            pytest.param(
                {"status": "ok", "data": [1], "provenance": None}, id="ok-without-provenance"
            ),
            pytest.param(
                {"status": "ok", "data": [1], "provenance": PROV, "missing": ("x",)},
                id="ok-with-missing",
            ),
            pytest.param(
                {"status": "partial", "data": [1], "provenance": PROV}, id="partial-without-missing"
            ),
            pytest.param(
                {"status": "error", "data": [], "provenance": None, "error": "boom"},
                id="error-with-empty-data",
            ),
            pytest.param(
                {"status": "error", "data": None, "provenance": None}, id="error-no-reason"
            ),
            pytest.param(
                {"status": "ok", "data": [1], "provenance": PROV, "error": "x"}, id="ok-with-error"
            ),
            pytest.param(
                {
                    "status": "error",
                    "data": None,
                    "provenance": None,
                    "error": "x",
                    "missing": ("a",),
                },
                id="error-with-missing",
            ),
            pytest.param(
                {"status": "unavailable", "data": None, "provenance": None, "error": ""},
                id="unavailable-empty-reason",
            ),
        ],
    )
    def test_inconsistent_results_are_rejected(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            ToolResult[list[int]].model_validate(kwargs)

    def test_failed_refuses_success_status(self) -> None:
        with pytest.raises(ValueError, match="ERROR or UNAVAILABLE"):
            ToolResult[int].failed("x", status=DataStatus.OK)

    def test_partial_lists_missing_fields(self) -> None:
        result = ToolResult[dict[str, int]].partial({"a": 1}, PROV, missing=("b",))
        assert result.succeeded
        assert result.missing == ("b",)


class TestNaiveDatetimeRejected:
    def test_provenance(self) -> None:
        with pytest.raises(ValidationError):
            Provenance(source="fake", retrieved_at=datetime(2026, 1, 1), source_ref=None)  # noqa: DTZ001

    def test_time_range(self) -> None:
        with pytest.raises(ValidationError):
            TimeRange.model_validate({"start": "2026-10-14T09:00:00", "end": "2026-10-14T10:00:00"})

    def test_event_draft(self) -> None:
        with pytest.raises(ValidationError):
            EventDraft.model_validate(
                {"title": "x", "start": "2026-10-14T09:00:00", "end": "2026-10-14T10:00:00+08:00"}
            )


class TestStrictInstants:
    @pytest.mark.parametrize("value", [1_700_000_000, "1700000000", 1.7e9, 1_700_000_000_000])
    def test_numeric_timestamps_are_rejected(self, value: object) -> None:
        with pytest.raises(ValidationError):
            TimeRange.model_validate({"start": value, "end": "2030-01-01T00:00:00+00:00"})

    def test_sub_minute_offsets_are_rejected(self) -> None:
        odd = timezone(timedelta(seconds=30))
        with pytest.raises(ValidationError, match="whole number of minutes"):
            TimeRange(start=datetime(2026, 1, 1, tzinfo=odd), end=datetime(2026, 1, 2, tzinfo=UTC))


class TestCalendarModels:
    def test_time_range_requires_end_after_start(self) -> None:
        with pytest.raises(ValidationError):
            TimeRange(start=NOW, end=NOW)

    def test_event_draft_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            EventDraft.model_validate(
                {"title": "x", "start": NOW, "end": NOW + timedelta(hours=1), "risk": 0}
            )

    def test_event_draft_rejects_malformed_attendee(self) -> None:
        with pytest.raises(ValidationError):
            EventDraft(title="x", start=NOW, end=NOW + timedelta(hours=1), attendees=("nobody",))

    def test_calendar_event_has_no_silent_defaults(self) -> None:
        with pytest.raises(ValidationError) as exc:
            CalendarEvent.model_validate({"event_id": "e1", "start": NOW})
        missing = {err["loc"][0] for err in exc.value.errors() if err["type"] == "missing"}
        assert missing == {"title", "end", "location", "attendees"}

    def test_calendar_event_keeps_unknown_as_none(self) -> None:
        event = CalendarEvent(
            event_id="e1", title=None, start=NOW, end=None, location=None, attendees=None
        )
        assert event.end is None
        assert event.attendees is None

    def test_offsets_are_preserved(self) -> None:
        tr = TimeRange(start=NOW, end=datetime(2026, 10, 13, 2, 0, tzinfo=UTC))
        assert tr.start.utcoffset() == timedelta(hours=8)
