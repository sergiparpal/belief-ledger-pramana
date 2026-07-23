"""Stable ledger errors shared by infrastructure adapters and use cases."""


class StoreError(RuntimeError):
    """A storage adapter could not complete a ledger operation."""

    pass


class HashChainError(StoreError):
    """The tamper-evident event chain could not be verified."""

    pass


class LlmReservationError(StoreError):
    """An atomic LLM budget reservation could not be acquired."""

    pass


__all__ = ["HashChainError", "LlmReservationError", "StoreError"]
