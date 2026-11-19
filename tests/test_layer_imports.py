import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "redisdesk"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_core_does_not_import_other_layers():
    forbidden = ("redisdesk.adapters", "redisdesk.infra", "redisdesk.app", "PySide6", "redis", "paramiko")
    for path in (ROOT / "core").rglob("*.py"):
        for name in _imports(path):
            assert not any(name == f or name.startswith(f + ".") for f in forbidden), f"{path} imports {name}"


def test_services_do_not_import_qt():
    for path in (ROOT / "app" / "services").rglob("*.py"):
        for name in _imports(path):
            assert not name.startswith("PySide6"), path
            assert not name.startswith("redisdesk.app.ui"), path


def test_ui_does_not_import_adapters_or_worker():
    for path in (ROOT / "app" / "ui").rglob("*.py"):
        for name in _imports(path):
            assert not name.startswith("redisdesk.adapters"), path
            assert not name.startswith("redisdesk.worker"), path
