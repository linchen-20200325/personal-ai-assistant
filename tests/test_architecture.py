"""Static boundaries: vendor SDKs stay in adapters; only the CLI approves."""

import ast
from pathlib import Path

import personal_ai_assistant

PACKAGE = Path(personal_ai_assistant.__file__).parent
ADAPTER = PACKAGE / "adapters" / "anthropic_model.py"
CLI = PACKAGE / "cli.py"
CORE_DIRS = ("domain", "safety", "tools", "providers", "planning")
CORE_MODULES = ("audit.py", "clock.py", "config.py", "validation.py", "display.py")
VENDOR_SDKS = ("anthropic", "openai", "mcp", "httpx", "httpx2")


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


def test_only_the_adapter_imports_vendor_sdks() -> None:
    offenders = {
        p.relative_to(PACKAGE).as_posix(): sorted(i for i in _imports(p) if _top(i) in VENDOR_SDKS)
        for p in _modules()
        if p != ADAPTER
    }
    assert {k: v for k, v in offenders.items() if v} == {}


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
