"""Hermes directory-plugin entry point."""

from __future__ import annotations

from typing import Any


def register(ctx: Any) -> None:
    """Load the packaged implementation under Hermes' generated namespace."""

    from .belief_ledger_pramana.plugin import register as package_register

    package_register(ctx)


__all__ = ["register"]
