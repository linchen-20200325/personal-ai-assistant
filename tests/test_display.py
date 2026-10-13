"""Terminal rendering cannot be visually spoofed."""

import pytest

from personal_ai_assistant.display import (
    escape_display,
    render_data,
    render_params,
    render_params_json,
)


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("plain 中文 text", "plain 中文 text"),
        ("a\nb", "a\\u000ab"),
        ("\x1b[31mred", "\\u001b[31mred"),
        ("abc\u202edcba", "abc\\u202edcba"),
        ("zero\u200bwidth", "zero\\u200bwidth"),
        ("nb\u00a0sp", "nb\\u00a0sp"),
        ("line\u2028sep", "line\\u2028sep"),
        ("back\\u202e", "back\\\\u202e"),
        ("tag\U000e0041", "tag\\U000e0041"),
    ],
)
def test_escape_display(raw: str, shown: str) -> None:
    assert escape_display(raw) == shown


def test_render_params_sorted_and_complete() -> None:
    long_text = "x" * 5000
    lines = render_params({"b": [1, "\u202e"], "a": long_text})
    assert lines == [f"  a: {long_text}", '  b: [1, "\\u202e"]']
    assert render_params({}) == ["  (無參數)"]


def test_render_params_json_requires_object() -> None:
    assert render_params_json('{"k": "v"}') == ["  k: v"]
    with pytest.raises(ValueError, match="JSON object"):
        render_params_json("[1]")


def test_render_data_marks_truncation() -> None:
    lines = render_data({"text": "y" * 100}, limit=20)
    assert lines[-1].startswith("…（顯示已截斷")
    assert render_data([], limit=20) == ["[]"]
