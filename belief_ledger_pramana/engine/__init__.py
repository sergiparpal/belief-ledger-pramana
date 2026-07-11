"""Deterministic admission, priority, and JTMS-style defeat engine."""

from .defeat import RelabelResult, relabel
from .validity import ValidityResult, validate_belief

__all__ = ["RelabelResult", "ValidityResult", "relabel", "validate_belief"]
