"""Compatibility re-exports for host-neutral ledger errors."""

from belief_ledger_core.errors import HashChainError, LlmReservationError, StoreError

__all__ = ["HashChainError", "LlmReservationError", "StoreError"]
