"""Compatibility re-exports for the host-neutral configuration loader."""

from belief_ledger_core.config import (
    CoreConfigSnapshot,
    FrozenDict,
    FrozenList,
    freeze_config,
    load_core_config,
)

__all__ = [
    "CoreConfigSnapshot",
    "FrozenDict",
    "FrozenList",
    "freeze_config",
    "load_core_config",
]
