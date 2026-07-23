"""Yogyatā admission for negative searches (R3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AbsenceAssessment:
    admissible: bool
    event_kind: str
    reason: str
    validity: dict[str, Any]


def assess_negative_search(
    *,
    search_succeeded: bool,
    truncated: bool,
    corpus: str,
    scope: str,
    query: str,
    parameters: dict[str, Any],
    coverage: float,
    recall: float,
    min_coverage: float,
    min_recall: float,
) -> AbsenceAssessment:
    validity = {
        "search_succeeded": search_succeeded,
        "truncated": truncated,
        "corpus": corpus,
        "scope": scope,
        "query": query,
        "parameters": parameters,
        "coverage": coverage,
        "recall": recall,
    }
    reasons: list[str] = []
    if not search_succeeded:
        reasons.append("search failed")
    if truncated:
        reasons.append("search result was truncated")
    if not corpus or not scope or not query or not parameters:
        reasons.append("search provenance is incomplete")
    if coverage < min_coverage:
        reasons.append("coverage below yogyata threshold")
    if recall < min_recall:
        reasons.append("recall below yogyata threshold")
    if reasons:
        return AbsenceAssessment(False, "SEARCH_FAILED", "; ".join(reasons), validity)
    return AbsenceAssessment(True, "ABSENCE_ADMISSIBLE", "yogyata satisfied", validity)
