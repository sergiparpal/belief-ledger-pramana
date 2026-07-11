"""Frozen deterministic ablation accounting from executable fixture runs."""

from __future__ import annotations

from typing import Any


def ablation_report(measured_rates: dict[str, float]) -> dict[str, Any]:
    required = {
        "flat_baseline",
        "types_only",
        "defeat_only",
        "no_generation_contract",
        "no_gate",
        "full",
    }
    if set(measured_rates) != required:
        raise ValueError("ablation measurements do not match the frozen matrix")
    return {
        "flat_baseline": {"vikalpa_rate": measured_rates["flat_baseline"], "components": []},
        "types_only": {
            "vikalpa_rate": measured_rates["types_only"],
            "components": ["types"],
        },
        "defeat_only": {
            "vikalpa_rate": measured_rates["defeat_only"],
            "components": ["defeat"],
        },
        "no_generation_contract": {
            "vikalpa_rate": measured_rates["no_generation_contract"],
            "components": ["types", "defeat"],
        },
        "no_gate": {
            "vikalpa_rate": measured_rates["no_gate"],
            "components": ["types", "defeat", "generation_contract"],
        },
        "full": {
            "vikalpa_rate": measured_rates["full"],
            "components": ["types", "defeat", "generation_contract", "gate"],
        },
        "method": "Every value is recomputed from frozen Suite A responses with the named components removed; Suite C separately measures gate safety.",
    }
