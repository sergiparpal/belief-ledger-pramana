"""Citation validation and deterministic claim-to-belief support matching."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..engine.validity import normalize_content
from ..models import Belief, LintClaim, LintDisposition, Status
from .extract import ExtractedClaim, strip_citations

_WORD = re.compile(r"[\w.-]+", re.UNICODE)


def match_claim(
    claim: ExtractedClaim,
    beliefs: Mapping[str, Belief],
    *,
    pending_marker: str,
) -> LintClaim:
    valid_citations: list[str] = []
    invalid_reasons: list[str] = []
    for belief_id in claim.cited_beliefs:
        belief = beliefs.get(belief_id)
        if belief is None:
            invalid_reasons.append(f"missing citation {belief_id}")
        elif belief.status in {Status.OUT, Status.QUARANTINED}:
            invalid_reasons.append(f"citation {belief_id} is {belief.status.value}")
        elif belief.status is Status.PENDING and not claim.pending_marked:
            invalid_reasons.append(f"pending citation {belief_id} lacks {pending_marker}")
        else:
            valid_citations.append(belief_id)

    clean_claim = strip_citations(claim.text, pending_marker)
    for belief_id in valid_citations:
        belief = beliefs[belief_id]
        if deterministic_entailment(clean_claim, belief.content):
            disposition = (
                LintDisposition.PENDING_MARKED
                if belief.status is Status.PENDING
                else LintDisposition.GROUNDED
            )
            return LintClaim(
                claim.text,
                disposition,
                claim.cited_beliefs,
                (belief_id,),
                "valid citation deterministically entails claim",
            )

    # The generation contract requires an auditable citation.  A matching
    # ledger belief is useful diagnostic information, but it must not turn an
    # uncited assertion into a pass.
    for belief in sorted(beliefs.values(), key=lambda item: item.id):
        if belief.status is Status.IN and deterministic_entailment(clean_claim, belief.content):
            return LintClaim(
                claim.text,
                LintDisposition.VIKALPA,
                claim.cited_beliefs,
                (belief.id,),
                "active belief entails claim but its required citation was omitted",
            )
        if (
            belief.status is Status.PENDING
            and claim.pending_marked
            and deterministic_entailment(clean_claim, belief.content)
        ):
            return LintClaim(
                claim.text,
                LintDisposition.VIKALPA,
                claim.cited_beliefs,
                (belief.id,),
                "pending belief entails claim but its required citation was omitted",
            )

    reason = "; ".join(invalid_reasons) if invalid_reasons else "no active belief entails claim"
    return LintClaim(claim.text, LintDisposition.VIKALPA, claim.cited_beliefs, (), reason)


def deterministic_entailment(claim: str, belief: str) -> bool:
    left = normalize_content(claim)
    right = normalize_content(belief)
    if not left or not right:
        return False
    if left == right:
        return True
    left_tokens = _WORD.findall(left)
    right_tokens = _WORD.findall(right)
    if not left_tokens:
        return False
    # A deterministic shortcut must preserve predicate/argument order.  Any
    # paraphrase or reordered proposition is left to the bounded semantic
    # component instead of being accepted by an unordered bag of words.
    return left_tokens == right_tokens and _negation_parity(left) == _negation_parity(right)


def _negation_parity(text: str) -> bool:
    return bool(re.search(r"\b(?:not|no|never|without|doesn't|isn't|cannot)\b", text))
