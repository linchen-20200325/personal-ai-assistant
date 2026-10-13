"""Static boundaries: vendor SDKs stay in adapters; only the CLI approves."""

import ast
from pathlib import Path

import personal_ai_assistant

PACKAGE = Path(personal_ai_assistant.__file__).parent
ADAPTERS = PACKAGE / "adapters"
# Which vendor packages each adapter module may import.
ALLOWED_VENDORS = {
    "anthropic_model.py": {"anthropic"},
    "google_auth.py": {
        "google",
        "googleapiclient",
        "google_auth_oauthlib",
        "google_auth_httplib2",
        "httplib2",
        "keyring",
    },
    "google_calendar.py": {"google", "googleapiclient", "httplib2"},
}
CLI = PACKAGE / "cli.py"
CORE_DIRS = ("domain", "safety", "tools", "providers", "planning")
CORE_MODULES = ("audit.py", "clock.py", "config.py", "validation.py", "display.py")
VENDOR_SDKS = (
    "anthropic",
    "openai",
    "mcp",
    "httpx",
    "httpx2",
    "google",
    "googleapiclient",
    "google_auth_oauthlib",
    "google_auth_httplib2",
    "httplib2",
    "keyring",
    "requests",
)


def _modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _core() -> list[Path]:
    return [
        p
        for p in _modules()
        if p.relative_to(PACKAGE).parts[0] in CORE_DIRS
        or (p.parent == PACKAGE and p.name in CORE_MODULES)
    ]


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _top(name: str) -> str:
    return name.split(".")[0]


def test_core_list_is_not_empty() -> None:
    names = {p.relative_to(PACKAGE).as_posix() for p in _core()}
    assert "planning/orchestrator.py" in names
    assert "safety/executor.py" in names
    assert "audit.py" in names


def test_vendor_sdks_only_in_their_adapters() -> None:
    offenders = {}
    for path in _modules():
        allowed = ALLOWED_VENDORS.get(path.name, set()) if path.parent == ADAPTERS else set()
        bad = sorted(i for i in _imports(path) if _top(i) in VENDOR_SDKS and _top(i) not in allowed)
        if bad:
            offenders[path.relative_to(PACKAGE).as_posix()] = bad
    assert offenders == {}


def test_google_calendar_adapter_requests_readonly_scope_only() -> None:
    source = (ADAPTERS / "google_auth.py").read_text(encoding="utf-8")
    scopes = {
        token
        for token in source.replace('"', " ").split()
        if token.startswith("https://www.googleapis.com/auth/") or token.startswith("https://mail")
    }
    assert scopes == {"https://www.googleapis.com/auth/calendar.readonly"}


def test_core_never_imports_adapters_or_cli() -> None:
    forbidden = ("personal_ai_assistant.adapters", "personal_ai_assistant.cli")
    offenders = {
        p.relative_to(PACKAGE).as_posix(): sorted(i for i in _imports(p) if i.startswith(forbidden))
        for p in _core()
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_only_cli_approves_pending_actions() -> None:
    callers = set()
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "approve"
            ):
                callers.add(path.relative_to(PACKAGE).as_posix())
    assert callers == {"cli.py"}


def test_planning_has_no_confirmation_access() -> None:
    for path in (PACKAGE / "planning").rglob("*.py"):
        imports = _imports(path)
        assert "personal_ai_assistant.safety.confirmation" not in imports, path.name
        assert "ConfirmationStore" not in path.read_text(encoding="utf-8"), path.name
