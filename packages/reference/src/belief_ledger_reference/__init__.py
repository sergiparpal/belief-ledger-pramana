"""Standalone strict adapter for Belief Ledger Core."""

from .runner import (
    DeliveryOutcome,
    DispatchPermit,
    DispatchResult,
    ReferenceAuthorization,
    ReferenceRunner,
)

__version__ = "1.0.0rc2"

__all__ = [
    "DeliveryOutcome",
    "DispatchPermit",
    "DispatchResult",
    "ReferenceAuthorization",
    "ReferenceRunner",
]
