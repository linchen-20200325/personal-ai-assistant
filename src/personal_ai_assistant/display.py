"""Terminal-safe rendering of untrusted text.

Anything shown to the human (planner text, provider data, confirmation
params) goes through ``escape_display`` so control, bidi-override, zero-width
and other invisible characters cannot hide or reorder what is being approved.
Escapes are unambiguous because a literal backslash is escaped too.
"""

import json
import unicodedata
from typing import Any

# Cc control, Cf format (bidi overrides, zero-width), Cs/Co/Cn surrogate,
# private-use, unassigned, Zl/Zp line/paragraph separators, Mn/Me combining
# marks (overlays such as U+0336 strike-through, variation selectors).
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Mn", "Me"})
# Letters / symbols that render as blank space.
_BLANK_LOOKALIKES = frozenset("\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0")


def _escape_char(ch: str) -> str:
    if ch == "\\":
        return "\\\\"
    category = unicodedata.category(ch)
    if (
        category in _HIDDEN_CATEGORIES
        or (category == "Zs" and ch != " ")
        or ch in _BLANK_LOOKALIKES
    ):
        code = ord(ch)
        return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"
    return ch


def escape_display(text: str) -> str:
    """Escape invisible characters, and every space after the first in a run.

    Space runs are escaped so a value cannot pad itself onto what looks like a
    separate, program-written line of the confirmation screen.
    """
    out: list[str] = []
    previous = ""
    for ch in text:
        out.append("\\u0020" if ch == " " and previous == " " else _escape_char(ch))
        previous = ch
    return "".join(out)


def _value_text(value: Any) -> str:
    # Strings are quoted so the end of an untrusted value is always visible.
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def render_params(params: dict[str, Any]) -> list[str]:
    """One ``key: value`` line per parameter, fully escaped, never truncated."""
    if not params:
        return ["  (無參數)"]
    return [
        f"  {escape_display(str(key))}: {escape_display(_value_text(params[key]))}"
        for key in sorted(params)
    ]


def render_params_json(params_json: str) -> list[str]:
    """Render the exact canonical params a pending action is bound to."""
    params = json.loads(params_json)
    if not isinstance(params, dict):
        raise ValueError("pending params must be a JSON object")
    return render_params(params)


def render_data(data: Any, *, limit: int) -> list[str]:
    """Pretty JSON of provider data, escaped line by line; truncation is explicit."""
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    truncated = len(text) > limit
    lines = []
    for line in text[:limit].splitlines():
        body = line.lstrip(" ")
        lines.append(" " * (len(line) - len(body)) + escape_display(body))  # keep indentation
    if truncated:
        lines.append(f"…（顯示已截斷，共 {len(text)} 字元）")
    return lines
