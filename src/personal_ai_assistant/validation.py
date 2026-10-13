"""Safe summaries of validation errors for audit logs and untrusted callers."""

import re
from typing import Any

from pydantic import ValidationError

_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def validation_summary(exc: ValidationError) -> str:
    """Error locations and types only; no input values or untrusted key names."""

    def loc(err: Any) -> str:
        parts = [
            str(p) if isinstance(p, int) or _FIELD_NAME.fullmatch(str(p)) else "<key>"
            for p in err["loc"]
        ]
        if err["type"] == "extra_forbidden" and parts:
            parts[-1] = "<extra>"  # the key itself is planner-supplied
        return ".".join(parts) or "<root>"

    return "; ".join(
        f"{loc(err)}: {err['type']}"
        for err in exc.errors(include_url=False, include_input=False, include_context=False)
    )
