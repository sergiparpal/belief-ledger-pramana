#!/usr/bin/env python3
"""Enforce one-way imports across all Belief Ledger distributions."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACES = {
    "core": ROOT / "packages/core/src/belief_ledger_core",
    "gateway": ROOT / "packages/gateway/src/belief_ledger_gateway",
    "reference": ROOT / "packages/reference/src/belief_ledger_reference",
    "mcp": ROOT / "packages/mcp/src/belief_ledger_mcp",
    "hermes": ROOT / "belief_ledger_pramana",
}
FORBIDDEN = {
    "core": (
        "belief_ledger_gateway",
        "belief_ledger_reference",
        "belief_ledger_mcp",
        "belief_ledger_pramana",
        "hermes",
        "hermes_agent",
        "hermes_cli",
    ),
    "gateway": (
        "belief_ledger_reference",
        "belief_ledger_mcp",
        "belief_ledger_pramana",
        "hermes",
        "hermes_agent",
        "hermes_cli",
    ),
    "reference": (
        "belief_ledger_mcp",
        "belief_ledger_pramana",
        "hermes",
        "hermes_agent",
        "hermes_cli",
    ),
    "mcp": (
        "belief_ledger_reference",
        "belief_ledger_pramana",
        "hermes",
        "hermes_agent",
        "hermes_cli",
    ),
    "hermes": (),
}


def imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append((node.lineno, node.module))
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            dynamic = (isinstance(node.func, ast.Name) and node.func.id == "__import__") or (
                isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            )
            if dynamic:
                result.append((node.lineno, node.args[0].value))
    return result


def workspace_violations() -> list[str]:
    failures: list[str] = []
    for surface, root in SURFACES.items():
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            for line, module in imported_modules(path):
                if any(
                    module == item or module.startswith(f"{item}.") for item in FORBIDDEN[surface]
                ):
                    failures.append(f"{path.relative_to(ROOT)}:{line}: {surface} imports {module}")
    return failures


def main() -> int:
    failures = workspace_violations()
    if failures:
        print("\n".join(failures))
        return 1
    print("workspace dependency directions are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
