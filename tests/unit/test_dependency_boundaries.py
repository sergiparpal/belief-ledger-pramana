from __future__ import annotations

from pathlib import Path

from scripts.check_dependency_boundaries import violations
from scripts.check_workspace_boundaries import imported_modules, workspace_violations


def test_dependency_boundary_detects_static_and_dynamic_hermes_imports(tmp_path: Path) -> None:
    safe = tmp_path / "safe.py"
    safe.write_text("import json\n", encoding="utf-8")
    assert violations(safe) == []
    unsafe = tmp_path / "unsafe.py"
    unsafe.write_text(
        "import hermes_agent\nimport importlib\nimportlib.import_module('hermes_cli.plugins')\n",
        encoding="utf-8",
    )
    found = violations(unsafe)
    assert len(found) == 2


def test_workspace_boundary_parser_finds_literal_dynamic_imports(tmp_path: Path) -> None:
    module = tmp_path / "module.py"
    module.write_text(
        "import belief_ledger_core\n"
        "import importlib\n"
        "importlib.import_module('belief_ledger_pramana.plugin')\n",
        encoding="utf-8",
    )
    assert imported_modules(module) == [
        (1, "belief_ledger_core"),
        (2, "importlib"),
        (3, "belief_ledger_pramana.plugin"),
    ]
    assert workspace_violations() == []
