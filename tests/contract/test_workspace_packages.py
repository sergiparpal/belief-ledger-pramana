from __future__ import annotations

import builtins
import importlib
import sys
import tomllib
from pathlib import Path


def test_core_imports_when_all_hermes_imports_are_rejected(monkeypatch) -> None:
    for name in tuple(sys.modules):
        if name.startswith("belief_ledger_core"):
            sys.modules.pop(name)
    original = builtins.__import__

    def guarded(name: str, *args, **kwargs):
        if name.split(".", 1)[0] in {
            "hermes",
            "hermes_agent",
            "hermes_cli",
            "hermes_constants",
        }:
            raise ModuleNotFoundError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    core = importlib.import_module("belief_ledger_core")
    assert core.__version__ == "1.0.0rc2"
    assert core.HostCapabilities().maximum_profile().value == "observe"


def test_workspace_versions_and_built_constraints_are_synchronized() -> None:
    root = Path(__file__).parents[2]
    hermes = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = tomllib.loads((root / "packages/core/pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    reference = tomllib.loads(
        (root / "packages/reference/pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert hermes["version"] == core["version"] == reference["version"] == "1.0.0rc2"
    assert "belief-ledger-core==1.0.0rc2" in hermes["dependencies"]
    assert "belief-ledger-core==1.0.0rc2" in reference["dependencies"]
