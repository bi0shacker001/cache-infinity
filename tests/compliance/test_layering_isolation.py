"""Static compliance checks for layering and feature isolation."""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "app"


def _iter_py_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.is_file()]


def _imports_in_file(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.add(module)
    return imports


def _find_importers(module_prefix: str) -> list[Path]:
    offenders: list[Path] = []
    for path in _iter_py_files(APP_ROOT):
        imports = _imports_in_file(path)
        if any(name == module_prefix or name.startswith(module_prefix + ".") for name in imports):
            offenders.append(path)
    return offenders


def test_db_schema_only_imported_by_dbmanage():
    offenders = [
        path
        for path in _find_importers("db.schema")
        if path != APP_ROOT / "db" / "dbmanage.py"
    ]
    assert offenders == [], f"db.schema imported outside db/dbmanage.py: {offenders}"


def test_db_adapter_only_imported_by_dbmanage():
    offenders = [
        path
        for path in _find_importers("db.adapter")
        if path != APP_ROOT / "db" / "dbmanage.py"
    ]
    assert offenders == [], f"db.adapter imported outside db/dbmanage.py: {offenders}"


def test_db_backends_only_imported_by_adapter():
    offenders = []
    for path in _iter_py_files(APP_ROOT):
        imports = _imports_in_file(path)
        matches = [
            name
            for name in imports
            if name == "db.backends"
            or name.startswith("db.backends.")
            or name == "backends"
            or name.startswith("backends.")
        ]
        if matches and path != APP_ROOT / "db" / "adapter.py":
            offenders.append(path)
    assert offenders == [], f"db.backends imported outside db/adapter.py: {offenders}"


def test_auth_module_not_imported_by_storage():
    offenders = []
    for path in _iter_py_files(APP_ROOT / "storage"):
        if any(name.startswith("auth.") or name == "auth" for name in _imports_in_file(path)):
            offenders.append(path)
    assert offenders == [], f"auth imported in storage modules: {offenders}"


def test_net_access_only_in_net_modules():
    net_indicators = {
        "requests",
        "urllib.request",
        "urllib.error",
        "http.client",
        "socket",
        "pycurl",
        "rclone_python",
    }
    offenders = []
    for path in _iter_py_files(APP_ROOT):
        if (APP_ROOT / "net") in path.parents:
            continue
        imports = _imports_in_file(path)
        if any(
            name == indicator or name.startswith(indicator + ".")
            for indicator in net_indicators
            for name in imports
        ):
            offenders.append(path)
    assert offenders == [], f"Network libraries imported outside app/net: {offenders}"


def test_server_module_is_loop_only():
    server_path = APP_ROOT / "core" / "server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8"))
    class_defs = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert class_defs == [], "app/core/server.py should not define classes"
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "run_server"
        for node in tree.body
    ), "app/core/server.py should define run_server"


def test_core_server_not_imported_outside_entrypoints():
    allowed = {
        APP_ROOT / "core" / "server.py",
        APP_ROOT / "cacheinfinity.py",
    }
    offenders = []
    for path in _iter_py_files(APP_ROOT):
        if path in allowed:
            continue
        imports = _imports_in_file(path)
        if any(name == "core.server" or name.startswith("core.server.") for name in imports):
            offenders.append(path)
    assert offenders == [], f"core.server imported outside entrypoints: {offenders}"


def _only_in_hosting(paths: list[Path]) -> list[Path]:
    return [path for path in paths if (APP_ROOT / "hosting") not in path.parents]


def test_hosting_modules_only_imported_by_core_services():
    allowed = {APP_ROOT / "core" / "services.py"}
    for module in ("hosting.webdav", "hosting.dispatcher", "hosting.browser_interface"):
        offenders = [
            path
            for path in _find_importers(module)
            if path not in allowed
        ]
        assert offenders == [], f"{module} imported outside core/services.py: {offenders}"


def test_hosting_frontend_only_imported_by_hosting_modules():
    offenders = _only_in_hosting(_find_importers("hosting.frontend"))
    assert offenders == [], f"hosting.frontend imported outside app/hosting: {offenders}"


def test_ui_api_only_imported_by_core_services_or_hosting():
    allowed = {APP_ROOT / "core" / "services.py"}
    offenders = []
    for path in _find_importers("ui.api"):
        if path in allowed:
            continue
        if (APP_ROOT / "hosting") in path.parents:
            continue
        offenders.append(path)
    assert offenders == [], f"ui.api imported outside core/services.py or hosting: {offenders}"


def test_ui_cli_only_imported_by_core_services():
    offenders = [
        path
        for path in _find_importers("ui.cli")
        if path != APP_ROOT / "core" / "services.py"
    ]
    assert offenders == [], f"ui.cli imported outside core/services.py: {offenders}"


def test_ui_backend_only_imported_by_ui_modules():
    offenders = [
        path
        for path in _find_importers("ui.backend")
        if (APP_ROOT / "ui") not in path.parents
    ]
    assert offenders == [], f"ui.backend imported outside app/ui: {offenders}"


def test_ui_webcore_only_imported_by_core_services():
    offenders = [
        path
        for path in _find_importers("ui.web.webcore")
        if path != APP_ROOT / "core" / "services.py"
    ]
    assert offenders == [], f"ui.web.webcore imported outside core/services.py: {offenders}"
