"""Hermes directory-plugin entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def register(ctx: Any) -> None:
    """Load the packaged implementation under Hermes' generated namespace."""

    workspace_core = Path(__file__).resolve().parent / "packages" / "core" / "src"
    if workspace_core.is_dir() and str(workspace_core) not in sys.path:
        sys.path.insert(0, str(workspace_core))
    from .belief_ledger_pramana.plugin import register as package_register

    package_register(ctx)


__all__ = ["register"]
