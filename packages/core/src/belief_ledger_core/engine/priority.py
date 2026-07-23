"""Visible lexicographic priority traces and fixed defeat rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import Belief, Pramana, Source
from .trust import effective_competence


@dataclass(frozen=True, slots=True)
class PriorityTrace:
    belief_id: str
    integrity_rank: int
    type_rank: int
    reliability_rank: int
    specificity_rank: int
    recency_rank: int
    type_key: str
    reliability: float

    @property
    def value(self) -> tuple[int, int, int, int, int]:
        return (
            self.integrity_rank,
            self.type_rank,
            self.reliability_rank,
            self.specificity_rank,
            self.recency_rank,
        )


@dataclass(frozen=True, slots=True)
class PriorityComparison:
    result: int
    attacker: PriorityTrace
    target: PriorityTrace
    decisive_field: str
    fixed_rule: str | None = None


def priority_trace(belief: Belief, source: Source, config: dict[str, Any]) -> PriorityTrace:
    priority = config["priority"]
    integrity_rank = int(priority["integrity_rank"][source.integrity.value])
    reliability = effective_competence(source, belief.domain, config)
    type_key = _type_key(belief, reliability, config)
    type_ranks = priority["type_rank"]["default"]
    domain_ranks = priority.get("domain_profiles", {}).get(belief.domain, {})
    type_rank = int(
        domain_ranks.get(
            type_key, domain_ranks.get(belief.pramana.value, type_ranks.get(type_key, 0))
        )
    )
    reliability_rank = round(reliability * 1_000)
    specificity_keys = priority.get("specificity_keys", [])
    specificity = sum(1 for key in specificity_keys if belief.qualifiers.get(str(key)))
    recency = 0
    if belief.perishability.value in {"fast", "live"}:
        recency = int(_timestamp(belief.observed_at))
    return PriorityTrace(
        belief.id,
        integrity_rank,
        type_rank,
        reliability_rank,
        specificity,
        recency,
        type_key,
        reliability,
    )


def compare_priority(
    attacker: Belief,
    target: Belief,
    sources: Mapping[str, Source],
    config: dict[str, Any],
) -> PriorityComparison:
    attacker_trace = priority_trace(attacker, sources[attacker.source_id], config)
    target_trace = priority_trace(target, sources[target.source_id], config)

    # Positive evidence always defeats an admitted absence (spec §3 and §4.2).
    if target.pramana is Pramana.ANUPALABDHI and attacker.pramana is not Pramana.ANUPALABDHI:
        return PriorityComparison(
            1, attacker_trace, target_trace, "fixed_rule", "positive_over_anupalabdhi"
        )
    if attacker.pramana is Pramana.ANUPALABDHI and target.pramana is not Pramana.ANUPALABDHI:
        return PriorityComparison(
            -1, attacker_trace, target_trace, "fixed_rule", "positive_over_anupalabdhi"
        )

    fields = ("integrity", "type", "reliability", "specificity", "recency")
    for index, (left, right) in enumerate(
        zip(attacker_trace.value, target_trace.value, strict=True)
    ):
        if left != right:
            return PriorityComparison(
                1 if left > right else -1, attacker_trace, target_trace, fields[index]
            )
    return PriorityComparison(0, attacker_trace, target_trace, "equal")


def _type_key(belief: Belief, reliability: float, config: dict[str, Any]) -> str:
    if belief.pramana is Pramana.SHABDA:
        bands = config.get("priority", {}).get("reliability_bands", {})
        high = float(bands.get("high", 0.8))
        medium = float(bands.get("medium", 0.5))
        band = "hi" if reliability >= high else "mid" if reliability >= medium else "lo"
        return f"shabda_apta_{band}"
    if belief.pramana is Pramana.ANUMANA:
        audited = any(
            justification.audit is not None and not justification.audit.fallacies
            for justification in belief.justifications
        )
        return "anumana_audited" if audited else "anumana_raw"
    return belief.pramana.value


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        raise ValueError("belief observed_at must be timezone-aware")
    return value.timestamp()
