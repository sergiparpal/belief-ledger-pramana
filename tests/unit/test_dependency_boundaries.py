from __future__ import annotations

from pathlib import Path

from scripts.check_dependency_boundaries import violations


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
