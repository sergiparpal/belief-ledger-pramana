"""LOW/MED/HIGH/CRITICAL bounded final-output policy."""

from __future__ import annotations

from collections.abc import Callable

from ..models import LintClaim, LintDisposition, LintReport, Stakes


def enforce_report(
    response: str,
    report: LintReport,
    *,
    stakes: Stakes,
    policy: dict[str, str],
    relint: Callable[[str], LintReport] | None = None,
    rewrite_once: Callable[[str], str] | None = None,
    max_rewrite_attempts: int = 1,
) -> LintReport:
    if report.passed:
        return report
    action = policy[stakes.value]
    unsupported = tuple(
        claim for claim in report.claims if claim.disposition is LintDisposition.VIKALPA
    )
    if action in {"allow", "annotate"}:
        warning = f"Grounding warning: {len(unsupported)} unsupported factual claim(s)."
        replacement = f"{response}\n\n{warning}" if action == "annotate" else None
        return LintReport(report.claims, True, replacement, (warning,))
    if (
        action == "rewrite_once"
        and max_rewrite_attempts > 0
        and rewrite_once is not None
        and relint is not None
    ):
        try:
            rewritten = rewrite_once(response)
        except Exception:
            fallback = _mark_unsupported(response, report)
            final = relint(fallback)
            return _marked_or_blocked(
                final, fallback, "rewrite unavailable; unsupported clauses marked"
            )
        second = relint(rewritten)
        if second.passed:
            return LintReport(second.claims, True, rewritten, ("response rewritten once",))
        fallback = _mark_unsupported(rewritten, second)
        final = relint(fallback)
        return _marked_or_blocked(final, fallback, "unsupported clauses marked after one rewrite")
    if action == "rewrite_once" and relint is not None:
        fallback = _mark_unsupported(response, report)
        checked = relint(fallback)
        return _marked_or_blocked(checked, fallback, "unsupported clauses marked")
    # `rewrite_once` without a re-lint callback cannot verify that marking made the response
    # acceptable, so the policy degrades to the blocking branch rather than returning
    # unverified text.
    return _blocked(report, unsupported)


def _marked_or_blocked(checked: LintReport, marked: str, warning: str) -> LintReport:
    """Return the marked text only when it actually passes; otherwise block.

    A non-passing report must never carry the candidate's own text as its replacement: the
    replacement is what callers deliver, and delivering it would defeat the block.
    """

    if not checked.passed:
        unsupported = tuple(
            claim for claim in checked.claims if claim.disposition is LintDisposition.VIKALPA
        )
        return _blocked(checked, unsupported)
    return LintReport(checked.claims, True, marked, (warning,))


def _blocked(report: LintReport, unsupported: tuple[LintClaim, ...]) -> LintReport:
    missing = [f"- unsupported candidate: {claim.text[:240]}" for claim in unsupported]
    replacement = (
        "Response blocked by belief-ledger grounding policy.\n\n"
        + ("\n".join(missing) if missing else "- grounding evaluation failed")
        + "\n\nSafe next step: obtain read-only evidence for each candidate, then answer from IN beliefs."
    )
    return LintReport(report.claims, False, replacement, ("high-stakes response blocked",))


def linter_failure_response(stakes: Stakes, original: str) -> str:
    if stakes in {Stakes.HIGH, Stakes.CRITICAL}:
        return (
            "Response blocked because the grounding linter was unavailable. "
            "No high-stakes factual answer was accepted; retry after ledger diagnostics pass."
        )
    return f"{original}\n\nGrounding warning: the belief-ledger linter was unavailable."


def _mark_unsupported(response: str, report: LintReport) -> str:
    result = response
    # Mark each distinct text once. Two claims sharing the same text would otherwise consume
    # two `replace` calls with count=1, marking the first occurrence twice and leaving the
    # second unmarked.
    marked: set[str] = set()
    for claim in report.claims:
        if claim.disposition is LintDisposition.VIKALPA and claim.text not in marked:
            marked.add(claim.text)
            result = result.replace(claim.text, f"speculation: {claim.text}", 1)
    return result
