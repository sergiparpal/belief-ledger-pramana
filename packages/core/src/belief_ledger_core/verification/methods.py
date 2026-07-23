"""Safe recovery suggestions for verification methods."""

from __future__ import annotations

from ..models import VerificationMethod


def method_instruction(method: VerificationMethod, proposition: str) -> str:
    if method is VerificationMethod.CROSS_SOURCE:
        return f"Find an independently rooted source that asserts or refutes: {proposition}"
    if method is VerificationMethod.TOOL_RECHECK:
        return f"Use a read-only observable in the same environment to re-check: {proposition}"
    if method is VerificationMethod.CHAIN_AUDIT:
        return f"Audit all IN premises and the warrant for: {proposition}"
    return f"Ask the user for explicit confirmation of: {proposition}"
