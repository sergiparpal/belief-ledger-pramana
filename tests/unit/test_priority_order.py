"""Structural pins for the lexicographic defeat order.

The repository has twice documented a claim about defeat that the code did not support. These tests
exist so the claim in `engine/priority.py`'s docstring, in the specification's §1 and §4.2, and in
ADR 0010 cannot drift from the implementation without something going red.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from belief_ledger_pramana.config import packaged_yaml
from belief_ledger_pramana.engine.priority import (
    PriorityTrace,
    compare_priority,
    priority_trace,
)
from belief_ledger_pramana.models import (
    Belief,
    Integrity,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    Stakes,
    Status,
)

CONFIG = packaged_yaml("defaults.yaml")
OBSERVED = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _source(
    identifier: str,
    *,
    integrity: Integrity = Integrity.TRUSTED,
    competence: float = 0.5,
) -> Source:
    return Source(
        identifier,
        "ep_priority",
        SourceKind.TOOL,
        integrity,
        identifier,
        f"{identifier}.example",
        {"general": competence},
    )


def _belief(
    identifier: str,
    source: Source,
    *,
    pramana: Pramana = Pramana.SHABDA,
    perishability: Perishability = Perishability.SLOW,
    observed_at: datetime = OBSERVED,
) -> Belief:
    return Belief(
        identifier,
        "ep_priority",
        f"content of {identifier}",
        f"content of {identifier}",
        pramana,
        source.id,
        (),
        (),
        {},
        perishability,
        observed_at,
        Stakes.LOW,
        Status.IN,
        Status.IN,
    )


def test_priority_value_is_exactly_the_five_documented_keys_in_order() -> None:
    """The load-bearing structural pin: the tuple order cannot change silently."""
    trace = priority_trace(_belief("b1", _source("s1")), _source("s1"), CONFIG)

    assert trace.value == (
        trace.integrity_rank,
        trace.type_rank,
        trace.reliability_rank,
        trace.specificity_rank,
        trace.recency_rank,
    )
    assert len(trace.value) == 5


def test_priority_trace_field_names_and_order_match_the_documented_tuple() -> None:
    """A field reordered on the dataclass would silently reorder `value` with it."""
    ranks = [
        field.name for field in dataclasses.fields(PriorityTrace) if field.name.endswith("_rank")
    ]

    assert ranks == [
        "integrity_rank",
        "type_rank",
        "reliability_rank",
        "specificity_rank",
        "recency_rank",
    ]


def test_reliability_decides_a_contest_that_integrity_and_type_left_tied() -> None:
    """The third key does real work: this is the half of the claim that is easy to forget.

    PRATYAKSHA is used deliberately. For SHABDA the type key is itself banded on reliability, so a
    large competence gap would surface at the *second* key and never reach the third — see
    `test_for_shabda_a_competence_gap_across_a_band_boundary_is_decided_at_type`.
    """
    strong = _source("s_strong", competence=0.9)
    weak = _source("s_weak", competence=0.2)
    attacker = _belief("b_strong", strong, pramana=Pramana.PRATYAKSHA)
    target = _belief("b_weak", weak, pramana=Pramana.PRATYAKSHA)
    sources = {strong.id: strong, weak.id: weak}

    comparison = compare_priority(attacker, target, sources, CONFIG)

    assert comparison.attacker.integrity_rank == comparison.target.integrity_rank
    assert comparison.attacker.type_rank == comparison.target.type_rank
    assert comparison.decisive_field == "reliability"
    assert comparison.result == 1


def test_reliability_decides_a_shabda_contest_inside_one_band() -> None:
    """Testimony reaches the third key too, as long as both sides land in the same band."""
    strong = _source("s_hi_strong", competence=0.95)
    weak = _source("s_hi_weak", competence=0.85)
    attacker = _belief("b_hi_strong", strong)
    target = _belief("b_hi_weak", weak)
    sources = {strong.id: strong, weak.id: weak}

    comparison = compare_priority(attacker, target, sources, CONFIG)

    assert comparison.attacker.type_key == comparison.target.type_key == "shabda_apta_hi"
    assert comparison.decisive_field == "reliability"
    assert comparison.result == 1


def test_for_shabda_a_competence_gap_across_a_band_boundary_is_decided_at_type() -> None:
    """`reliability` is not confined to the third key for testimony.

    `_type_key` bands SHABDA on the same `effective_competence` scalar, so competence also moves
    `type_rank`. The claim that a scalar participates "only as the third key" is therefore true of
    the tuple's construction but not of the scalar's influence, and this test pins the difference
    so the documentation cannot quietly overstate the separation.
    """
    high = _source("s_band_hi", competence=0.85)
    medium = _source("s_band_mid", competence=0.75)
    attacker = _belief("b_band_hi", high)
    target = _belief("b_band_mid", medium)
    sources = {high.id: high, medium.id: medium}

    comparison = compare_priority(attacker, target, sources, CONFIG)

    assert comparison.attacker.type_key == "shabda_apta_hi"
    assert comparison.target.type_key == "shabda_apta_mid"
    assert comparison.decisive_field == "type"
    assert comparison.result == 1


def test_reliability_can_never_override_integrity() -> None:
    """The other half: a scalar cannot beat a structural key that already differs."""
    competent_but_untrusted = _source("s_untrusted", integrity=Integrity.UNTRUSTED, competence=0.95)
    incompetent_but_trusted = _source("s_trusted", integrity=Integrity.TRUSTED, competence=0.05)
    attacker = _belief("b_untrusted", competent_but_untrusted)
    target = _belief("b_trusted", incompetent_but_trusted)
    sources = {
        competent_but_untrusted.id: competent_but_untrusted,
        incompetent_but_trusted.id: incompetent_but_trusted,
    }

    comparison = compare_priority(attacker, target, sources, CONFIG)

    assert comparison.decisive_field == "integrity"
    assert comparison.attacker.reliability_rank > comparison.target.reliability_rank
    assert comparison.result == -1, "the reliability-favoured belief must still lose"


def test_reliability_can_never_override_type() -> None:
    competent = _source("s_competent", competence=0.95)
    incompetent = _source("s_incompetent", competence=0.05)
    attacker = _belief("b_shabda", competent, pramana=Pramana.SHABDA)
    target = _belief("b_pratyaksha", incompetent, pramana=Pramana.PRATYAKSHA)
    sources = {competent.id: competent, incompetent.id: incompetent}

    comparison = compare_priority(attacker, target, sources, CONFIG)

    assert comparison.attacker.integrity_rank == comparison.target.integrity_rank
    assert comparison.decisive_field == "type"
    assert comparison.attacker.reliability_rank > comparison.target.reliability_rank
    assert comparison.result == -1


@pytest.mark.parametrize("confidence", [None, 0.0, 1.0])
def test_the_beliefs_own_confidence_field_is_never_read(confidence: float | None) -> None:
    """`Belief.confidence` is auxiliary in the strict sense: priority ignores it entirely."""
    source = _source("s_conf")
    baseline = _belief("b_conf", source)
    with_confidence = dataclasses.replace(baseline, confidence=confidence)

    assert (
        priority_trace(with_confidence, source, CONFIG).value
        == priority_trace(baseline, source, CONFIG).value
    )


def test_positive_evidence_beats_an_absence_regardless_of_the_tuple() -> None:
    """The fixed rule sits outside the lexicographic order and outranks all five keys."""
    weak = _source("s_weak_absence", integrity=Integrity.UNTRUSTED, competence=0.05)
    strong = _source("s_strong_absence", integrity=Integrity.TRUSTED, competence=0.95)
    attacker = _belief("b_positive", weak, pramana=Pramana.SHABDA)
    target = _belief("b_absence", strong, pramana=Pramana.ANUPALABDHI)
    sources = {weak.id: weak, strong.id: strong}

    comparison = compare_priority(attacker, target, sources, CONFIG)

    assert comparison.decisive_field == "fixed_rule"
    assert comparison.fixed_rule == "positive_over_anupalabdhi"
    assert comparison.result == 1


def test_an_all_key_tie_is_samsaya_rather_than_an_arbitrary_winner() -> None:
    source = _source("s_tie")
    sources = {source.id: source}

    comparison = compare_priority(_belief("b_a", source), _belief("b_b", source), sources, CONFIG)

    assert comparison.result == 0
    assert comparison.decisive_field == "equal"
