"""Complete deterministic grounding report."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Belief, LintDisposition, LintReport
from .extract import extract_claims
from .match import match_claim


def lint_response(
    response: str,
    beliefs: Iterable[Belief],
    *,
    pending_marker: str,
    require_coverage: bool = False,
) -> LintReport:
    belief_map = {belief.id: belief for belief in beliefs}
    claims = tuple(
        match_claim(claim, belief_map, pending_marker=pending_marker)
        for claim in extract_claims(
            response, pending_marker=pending_marker, require_coverage=require_coverage
        )
    )
    passed = all(
        claim.disposition in {LintDisposition.GROUNDED, LintDisposition.PENDING_MARKED}
        for claim in claims
    )
    return LintReport(claims=claims, passed=passed)
