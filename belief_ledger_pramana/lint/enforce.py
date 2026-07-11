"""LOW/MED/HIGH/CRITICAL bounded final-output policy."""

from __future__ import annotations

from collections.abc import Callable

from ..models import LintDisposition, LintReport, Stakes


def enforce_report(
    response: str,
    report: LintReport,
    *,
    stakes: Stakes,
    policy: dict[str, str],
    relint: Callable[[str], LintReport] | None = None,
    rewrite_once: Callable[[str], str] | None = None,
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
        return LintReport(report.claims, False, replacement, (warning,))
    if action == "rewrite_once" and rewrite_once is not None and relint is not None:
        try:
            rewritten = rewrite_once(response)
        except Exception:
            fallback = _mark_unsupported(response, report)
            final = relint(fallback)
            return LintReport(
                final.claims,
                final.passed,
                fallback,
                ("rewrite unavailable; unsupported clauses marked",),
            )
        second = relint(rewritten)
        if second.passed:
            return LintReport(second.claims, True, rewritten, ("response rewritten once",))
        fallback = _mark_unsupported(rewritten, second)
        final = relint(fallback)
        return LintReport(
            final.claims, final.passed, fallback, ("unsupported clauses marked after one rewrite",)
        )
    if action == "rewrite_once":
        fallback = _mark_unsupported(response, report)
        checked = relint(fallback) if relint else report
        return LintReport(checked.claims, checked.passed, fallback, ("unsupported clauses marked",))
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
    for claim in report.claims:
        if claim.disposition is LintDisposition.VIKALPA:
            result = result.replace(claim.text, f"speculation: {claim.text}", 1)
    return result
