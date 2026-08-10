"""Visible lexicographic priority traces and fixed defeat rules.

Defeat is decided by a fixed lexicographic order over five keys, in this order:

    (integrity_rank, type_rank, reliability_rank, specificity_rank, recency_rank)

A scalar quantity does participate. `reliability_rank`, derived from `effective_competence`, is the
third key: it can decide a contest that `integrity_rank` and `type_rank` left tied, and it can never
override either. It is not a confidence score over the belief; it is a competence estimate for the
source, learned from that source's history of confirmations and defeats.

One qualification, and it matters. For SHABDA the same scalar also feeds the *second* key:
`_type_key` bands testimony into `shabda_apta_hi`/`_mid`/`_lo` by `effective_competence`, so a
competence gap that crosses a band boundary is decided at `type_rank` and never reaches
`reliability_rank`. "Third key" describes where the scalar sits in the tuple, not the full extent of
its influence on testimony. It remains true that no amount of competence can beat a differing
`integrity_rank`, and that for every other pramāṇa `type_rank` is independent of it.

`Belief.confidence` is a different thing and is never read here. That field is auxiliary in the
strict sense — no code path in this module consults it — which is what the specification's R1 means
when it says a scalar does not govern defeat. Read the two claims together: the belief's own scalar
never participates, and the source's competence participates only after two structural keys have
tied.

Two rules sit outside the tuple entirely. Positive evidence always defeats an admitted absence,
whatever the tuple says; and equality across all five keys is not a tie-break but saṃśaya, which
`relabel` turns into PENDING rather than an arbitrary winner.

The order is pinned structurally by `tests/unit/test_priority_order.py`, so it cannot drift from
this docstring silently. See `docs/adr/0010-scalar-competence-in-the-priority-order.md`.
"""

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
            justification.audit is not None
            and justification.audit.paksadharmata
            and justification.audit.sapakse_sattvam
            and justification.audit.vipakse_asattvam
            and not justification.audit.fallacies
            for justification in belief.justifications
        )
        return "anumana_audited" if audited else "anumana_raw"
    return belief.pramana.value


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        raise ValueError("belief observed_at must be timezone-aware")
    return value.timestamp()
