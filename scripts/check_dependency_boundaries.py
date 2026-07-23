#!/usr/bin/env python3
"""Reject Hermes imports and dynamic loads from the host-neutral core."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "packages" / "core" / "src" / "belief_ledger_core"
FORBIDDEN = ("hermes", "hermes_agent", "hermes_cli", "hermes_constants")


def violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "__import__")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                )
            )
        ):
            modules.append(node.args[0].value)
        for module in modules:
            if module in FORBIDDEN or module.startswith(tuple(f"{item}." for item in FORBIDDEN)):
                label = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                found.append(f"{label}:{node.lineno}: {module}")
    return found


def main() -> int:
    failures = [item for path in sorted(CORE.rglob("*.py")) for item in violations(path)]
    if failures:
        print("\n".join(failures))
        return 1
    print(f"core dependency boundary valid across {len(list(CORE.rglob('*.py')))} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
