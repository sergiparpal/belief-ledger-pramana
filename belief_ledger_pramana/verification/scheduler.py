"""Compatibility import for the moved verification application service."""

from ..application.verification import VerificationResult, VerificationScheduler

__all__ = ["VerificationResult", "VerificationScheduler"]
