"""Hermes directory-plugin entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def register(ctx: Any) -> None:
    """Load the packaged implementation under Hermes' generated namespace."""

    # Append, never insert at 0: this runs inside the host process, and taking priority over
    # every already-importable module would let a source checkout shadow the host's own
    # packages. An installed distribution provides `belief_ledger_core` already, so this
    # entry only matters when running from a workspace checkout.
    workspace_core = Path(__file__).resolve().parent / "packages" / "core" / "src"
    if workspace_core.is_dir() and str(workspace_core) not in sys.path:
        sys.path.append(str(workspace_core))
    from .belief_ledger_pramana.plugin import register as package_register

    package_register(ctx)


__all__ = ["register"]
