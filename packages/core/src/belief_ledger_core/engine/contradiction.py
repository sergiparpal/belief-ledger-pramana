"""Candidate blocking and deterministic contradiction adjudication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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


def candidate_pair(
    left: Belief,
    right: Belief,
    *,
    left_tokens: set[str] | None = None,
    right_tokens: set[str] | None = None,
) -> bool:
    if left.id == right.id:
        return False
    resolved_left_tokens = (
        left_tokens if left_tokens is not None else candidate_tokens(left.content)
    )
    resolved_right_tokens = (
        right_tokens if right_tokens is not None else candidate_tokens(right.content)
    )
    shared = resolved_left_tokens & resolved_right_tokens
    return bool(shared)


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
        and not _numeric_equal(left_numeric.group(2), right_numeric.group(2))
    ):
        return ContradictionDecision("rebut", "same predicate has unequal numeric values", scope)
    return ContradictionDecision("uncertain", "deterministic rules are insufficient", scope)


def _numeric_equal(left: str, right: str) -> bool:
    try:
        return Decimal(left) == Decimal(right)
    except InvalidOperation:
        return left == right


def candidate_tokens(content: str) -> set[str]:
    stop = {"the", "a", "an", "is", "are", "not", "of", "to", "in", "and", "or"}
    return {
        token
        for token in _TOKEN.findall(normalize_content(content))
        if token not in stop and len(token) > 1
    }
