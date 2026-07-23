"""Per-pramāṇa admission checks and content discipline."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..models import Belief, Pramana, Status

_DEICTIC = re.compile(
    r"\b(this|that|these|those|it|they|above|below|previous|former|latter|este|esta|eso|anterior)\b",
    re.IGNORECASE,
)
_CONJUNCTION = re.compile(r"(?:;|\b(?:and|plus|as well as|y|además)\b)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ValidityResult:
    valid: bool
    normalized_content: str
    reasons: tuple[str, ...]
    checks: dict[str, bool]


def normalize_content(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = normalized.rstrip(" .")
    return normalized.casefold()


def validate_content(
    content: str, *, max_words: int = 40, max_chars: int = 2_000
) -> tuple[str, ...]:
    reasons: list[str] = []
    stripped = re.sub(r"\s+", " ", content).strip()
    if not stripped:
        reasons.append("content is empty")
    if len(stripped.split()) > max_words:
        reasons.append(f"content exceeds {max_words} words")
    if len(stripped) > max_chars:
        reasons.append(f"content exceeds {max_chars} characters")
    if _CONJUNCTION.search(stripped):
        reasons.append("content appears non-atomic")
    if _DEICTIC.search(stripped):
        reasons.append("content is not self-contained")
    return tuple(reasons)


def validate_belief(
    belief: Belief,
    *,
    premise_statuses: Mapping[str, Status] | None = None,
    evidence_payloads: Mapping[str, str | None] | None = None,
    evidence_mode: str = "excerpt",
    max_words: int = 40,
    max_chars: int = 2_000,
    yogyata_min_coverage: float = 0.85,
    yogyata_min_recall: float = 0.85,
) -> ValidityResult:
    """Apply content and type-specific conditions without making trust decisions."""

    reasons = list(validate_content(belief.content, max_words=max_words, max_chars=max_chars))
    checks: dict[str, bool] = {
        "atomic": not any("atomic" in reason for reason in reasons),
        "self_contained": not any("self-contained" in reason for reason in reasons),
        "bounded_length": not any("exceeds" in reason for reason in reasons),
    }
    validity = belief.validity
    evidence_payloads = evidence_payloads or {}
    premise_statuses = premise_statuses or {}

    if belief.pramana is Pramana.PRATYAKSHA:
        _require(
            checks,
            reasons,
            "evidence_present",
            bool(belief.evidence),
            "pratyaksha requires evidence",
        )
        _require(
            checks,
            reasons,
            "tool_ok",
            bool(validity.get("tool_ok")),
            "tool did not complete successfully",
        )
        _require(
            checks,
            reasons,
            "parsed",
            bool(validity.get("parsed", True)),
            "tool output was not parsed",
        )
        _require(
            checks,
            reasons,
            "measured_only",
            bool(validity.get("measured_only")),
            "wrapper exceeds what the tool directly measured",
        )
        _require(
            checks,
            reasons,
            "environment_integrity",
            bool(validity.get("environment_integrity", True)),
            "tool environment integrity is not established",
        )
    elif belief.pramana is Pramana.SHABDA:
        _require(
            checks,
            reasons,
            "evidence_present",
            bool(belief.evidence),
            "shabda requires cited evidence",
        )
        _require(
            checks,
            reasons,
            "apta_calculable",
            validity.get("apta") is not None,
            "source apta is not calculable",
        )
        _require(
            checks,
            reasons,
            "assertive",
            bool(validity.get("assertive", True)),
            "source span is not assertive",
        )
        _validate_spans(belief, evidence_payloads, evidence_mode, checks, reasons)
    elif belief.pramana is Pramana.ANUMANA:
        _validate_derived(belief, premise_statuses, checks, reasons)
    elif belief.pramana is Pramana.ARTHAPATTI:
        _validate_derived(belief, premise_statuses, checks, reasons)
        explanandum = str(validity.get("explanandum", ""))
        _require(
            checks,
            reasons,
            "explanandum_in",
            bool(explanandum) and premise_statuses.get(explanandum) is Status.IN,
            "arthapatti requires an IN explanandum",
        )
        alternatives = validity.get("alternatives")
        _require(
            checks,
            reasons,
            "alternatives_recorded",
            isinstance(alternatives, Sequence)
            and not isinstance(alternatives, str)
            and bool(alternatives),
            "arthapatti requires recorded alternatives",
        )
    elif belief.pramana is Pramana.UPAMANA:
        _validate_derived(belief, premise_statuses, checks, reasons)
        _require(
            checks,
            reasons,
            "similarity_basis",
            bool(validity.get("similarity_basis")),
            "upamana requires an explicit similarity basis",
        )
    elif belief.pramana is Pramana.ANUPALABDHI:
        _require(
            checks,
            reasons,
            "evidence_present",
            bool(belief.evidence),
            "anupalabdhi requires search evidence",
        )
        _require(
            checks,
            reasons,
            "search_succeeded",
            bool(validity.get("search_succeeded")),
            "negative search failed",
        )
        _require(
            checks,
            reasons,
            "not_truncated",
            not bool(validity.get("truncated", True)),
            "negative search was truncated",
        )
        for required in ("corpus", "scope", "query", "parameters"):
            _require(
                checks,
                reasons,
                f"recorded_{required}",
                bool(validity.get(required)),
                f"anupalabdhi requires recorded {required}",
            )
        coverage = _float_or_zero(validity.get("coverage"))
        recall = _float_or_zero(validity.get("recall"))
        _require(
            checks,
            reasons,
            "coverage",
            coverage >= yogyata_min_coverage,
            "search coverage is below yogyata threshold",
        )
        _require(
            checks,
            reasons,
            "recall",
            recall >= yogyata_min_recall,
            "search recall is below yogyata threshold",
        )

    return ValidityResult(
        valid=not reasons,
        normalized_content=normalize_content(belief.content),
        reasons=tuple(reasons),
        checks=checks,
    )


def _validate_derived(
    belief: Belief,
    premise_statuses: Mapping[str, Status],
    checks: dict[str, bool],
    reasons: list[str],
) -> None:
    _require(
        checks,
        reasons,
        "justification_present",
        bool(belief.justifications),
        f"{belief.pramana.value} requires a justification",
    )
    for justification in belief.justifications:
        _require(
            checks,
            reasons,
            f"warrant_{justification.id}",
            bool(justification.warrant.strip()),
            "justification warrant is empty",
        )
        _require(
            checks,
            reasons,
            f"premises_{justification.id}",
            bool(justification.premises)
            and all(premise_statuses.get(item) is Status.IN for item in justification.premises),
            "all justification premises must be IN",
        )


def _validate_spans(
    belief: Belief,
    payloads: Mapping[str, str | None],
    evidence_mode: str,
    checks: dict[str, bool],
    reasons: list[str],
) -> None:
    if evidence_mode == "hash_only":
        _require(
            checks,
            reasons,
            "recoverable_span",
            False,
            "hash_only evidence cannot support content shabda",
        )
        return
    for ref in belief.evidence:
        payload = payloads.get(ref.evidence_id)
        valid = payload is not None and ref.span is not None
        if valid and ref.span is not None and payload is not None:
            start, end = ref.span
            valid = 0 <= start < end <= len(payload)
        _require(
            checks, reasons, f"span_{ref.evidence_id}", valid, "evidence span is not recoverable"
        )


def _require(
    checks: dict[str, bool], reasons: list[str], key: str, condition: bool, reason: str
) -> None:
    checks[key] = condition
    if not condition:
        reasons.append(reason)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
