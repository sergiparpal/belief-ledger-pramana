"""Frozen deterministic ablation accounting from executable fixture runs.

The specification's §10 names five configurations. Only three of them, plus the flat baseline, are
things the Suite A instrument can tell apart.

Suite A measures one quantity: the vikalpa rate that `lint_response(response, beliefs)` produces.
That function has exactly two inputs, so a configuration can only be expressed as a choice of
response text and a choice of belief list. Neither the defeat engine nor the action gate changes
either input — defeat decides belief *status* inside an episode, and the gate decides whether a
tool call proceeds, which the linter never observes. Before ADR 0017 both were nonetheless listed
with a rate: `defeat_only` was computed from the same pair as `flat_baseline`, and `no_gate` from
the same pair as `full`. They were identities, and they published as measurements. A reader
comparing `no_gate` against `full` saw the gate contribute exactly zero, which is what an
identity always shows and not what the gate does.

Those two configurations now report `measurable: false` and carry no rate. Suite B measures the
defeat engine (wrong winners, descendant propagation) and Suite C measures the gate (unsafe
actions reaching the handler, false-block rate); an ablation that reuses those instruments is
possible, and is design work rather than accounting. See
`docs/adr/0017-ablation-arms-the-suite-a-instrument-cannot-isolate.md`.
"""

from __future__ import annotations

from typing import Any

MEASURED_ARMS = (
    "flat_baseline",
    "types_only",
    "no_generation_contract",
    "full",
)

# Named by the specification's §10, not isolable by the Suite A vikalpa metric, and named here
# with the suite that does measure the component instead.
UNMEASURABLE_ARMS: dict[str, tuple[list[str], str]] = {
    "defeat_only": (
        ["defeat"],
        "The defeat engine changes belief status inside an episode; lint_response observes only "
        "the response text and the belief list it is handed. Suite B measures defeat directly.",
    ),
    "no_gate": (
        ["types", "defeat", "generation_contract"],
        "The action gate decides whether a tool call proceeds and never reaches the linter. "
        "Suite C measures gate safety directly.",
    ),
}


def ablation_report(measured_rates: dict[str, float]) -> dict[str, Any]:
    if set(measured_rates) != set(MEASURED_ARMS):
        raise ValueError("ablation measurements do not match the frozen matrix")
    report: dict[str, Any] = {
        "flat_baseline": {
            "vikalpa_rate": measured_rates["flat_baseline"],
            "components": [],
            "measurable": True,
        },
        "types_only": {
            "vikalpa_rate": measured_rates["types_only"],
            "components": ["types"],
            "measurable": True,
        },
        "no_generation_contract": {
            "vikalpa_rate": measured_rates["no_generation_contract"],
            "components": ["types", "defeat"],
            "measurable": True,
        },
        "full": {
            "vikalpa_rate": measured_rates["full"],
            "components": ["types", "defeat", "generation_contract", "gate"],
            "measurable": True,
        },
    }
    for name, (components, reason) in UNMEASURABLE_ARMS.items():
        report[name] = {
            "vikalpa_rate": None,
            "components": components,
            "measurable": False,
            "reason": reason,
        }
    report["method"] = (
        "Every rate is recomputed from frozen Suite A responses with the named components "
        "removed. The Suite A instrument is the vikalpa rate of lint_response, which observes "
        "only the response text and the belief list; configurations differing in neither carry "
        "no rate and are marked measurable: false. Suite B measures the defeat engine and "
        "Suite C measures the action gate."
    )
    return report
