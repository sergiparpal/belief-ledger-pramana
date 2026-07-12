"""Privacy-preserving evidence preparation for Hermes tool results."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..events import content_hash

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
)


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    payload: str | None
    full_hash: str
    redacted: bool
    excerpt_start: int | None
    excerpt_end: int | None
    observed_chars: int


def prepare_evidence(
    result: str, *, mode: str, max_excerpt_chars: int, redact: bool = True
) -> PreparedEvidence:
    """Hash the complete observed result, then redact before persistence."""

    full_hash = content_hash(result)
    redacted_text, redacted = redact_secrets(result) if redact else (result, False)
    if mode == "hash_only":
        return PreparedEvidence(None, full_hash, redacted, None, None, len(result))
    if mode == "full":
        return PreparedEvidence(
            redacted_text, full_hash, redacted, 0, len(redacted_text), len(result)
        )
    if mode != "excerpt":
        raise ValueError(f"unsupported evidence mode: {mode}")
    limit = max(0, max_excerpt_chars)
    excerpt = redacted_text[:limit]
    return PreparedEvidence(excerpt, full_hash, redacted, 0, len(excerpt), len(result))


def redact_secrets(text: str) -> tuple[str, bool]:
    result = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", result)
        elif pattern.groups == 1:
            result = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result, result != text
