"""Candidate blocking and deterministic contradiction adjudication."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Belief
from .qualifiers import ScopeReconciliation, reconcile_qualifiers
from .validity import normalize_content

_TOKEN = re.compile(r"[\w.-]+", re.UNICODE)
_NEGATIONS = (
    (" is not ", " is "),
    (" does not ", " does "),
    (" cannot ", " can "),
    (" no existe ", " existe "),
    (" is absent", " exists"),
    (" does not exist", " exists"),
)
_NUMERIC = re.compile(r"^(.*?)(?:=| is | equals )\s*([-+]?\d+(?:\.\d+)*)\s*([^\d]*)$")


@dataclass(frozen=True, slots=True)
class ContradictionDecision:
    outcome: str
    basis: str
    scope: ScopeReconciliation


def candidate_pair(left: Belief, right: Belief) -> bool:
    if left.id == right.id:
        return False
    left_tokens = _content_tokens(left.content)
    right_tokens = _content_tokens(right.content)
    shared = left_tokens & right_tokens
    return len(shared) >= 2 or (
        bool(shared)
        and (left.pramana.value == "anupalabdhi" or right.pramana.value == "anupalabdhi")
    )


def classify_deterministically(left: Belief, right: Belief) -> ContradictionDecision:
    scope = reconcile_qualifiers(left.qualifiers, right.qualifiers)
    if not scope.compatible:
        return ContradictionDecision("scope_mismatch", scope.reason, scope)
    ltext = f" {normalize_content(left.content)} "
    rtext = f" {normalize_content(right.content)} "
    if ltext == rtext:
        return ContradictionDecision("compatible", "normalized contents are equal", scope)
    for negative, positive in _NEGATIONS:
        if negative in ltext and ltext.replace(negative, positive) == rtext:
            return ContradictionDecision(
                "rebut", f"deterministic negation: {negative.strip()}", scope
            )
        if negative in rtext and rtext.replace(negative, positive) == ltext:
            return ContradictionDecision(
                "rebut", f"deterministic negation: {negative.strip()}", scope
            )
    left_numeric = _NUMERIC.match(ltext.strip())
    right_numeric = _NUMERIC.match(rtext.strip())
    if (
        left_numeric
        and right_numeric
        and left_numeric.group(1).strip() == right_numeric.group(1).strip()
        and left_numeric.group(3).strip() == right_numeric.group(3).strip()
        and left_numeric.group(2) != right_numeric.group(2)
    ):
        return ContradictionDecision("rebut", "same predicate has unequal numeric values", scope)
    return ContradictionDecision("uncertain", "deterministic rules are insufficient", scope)


def _content_tokens(content: str) -> set[str]:
    stop = {"the", "a", "an", "is", "are", "not", "of", "to", "in", "and", "or"}
    return {
        token
        for token in _TOKEN.findall(normalize_content(content))
        if token not in stop and len(token) > 1
    }
