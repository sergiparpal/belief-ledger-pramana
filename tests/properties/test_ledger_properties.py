from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from belief_ledger_pramana.context.budget import CharacterBudget
from belief_ledger_pramana.engine.defeat import relabel
from belief_ledger_pramana.engine.qualifiers import (
    canonicalize_qualifiers,
    reconcile_qualifiers,
)
from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.models import (
    Belief,
    EvidenceRef,
    IngestionSupport,
    Integrity,
    Justification,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    Status,
)


@given(
    parts=st.lists(st.text(max_size=300), max_size=30),
    maximum=st.integers(min_value=0, max_value=2_000),
)
def test_character_budget_is_hard_bounded(parts: list[str], maximum: int) -> None:
    budget = CharacterBudget(maximum)
    for index, part in enumerate(parts):
        budget.add(part, mandatory=index % 7 == 0)
    assert len(budget.render()) <= maximum
    assert budget.used == len(budget.render())


def test_mandatory_budget_truncation_uses_every_remaining_character() -> None:
    budget = CharacterBudget(7)
    assert budget.add("abc")
    assert not budget.add("123456", mandatory=True)
    assert budget.render() == "abc\n123"
    assert budget.used == 7
    assert budget.truncated


@given(
    left_scope=st.text(max_size=30),
    right_scope=st.text(max_size=30),
    left_units=st.sampled_from(("bytes", "byte", "seconds", "s", "kb")),
    right_units=st.sampled_from(("bytes", "b", "seconds", "sec", "kib")),
)
def test_qualifier_reconciliation_is_symmetric(
    left_scope: str,
    right_scope: str,
    left_units: str,
    right_units: str,
) -> None:
    left = {"scope": left_scope, "units": left_units, "ignored": "x"}
    right = {"scope": right_scope, "units": right_units, "ignored": "y"}
    forward = reconcile_qualifiers(left, right)
    reverse = reconcile_qualifiers(right, left)
    assert forward.compatible == reverse.compatible
    assert forward.normalized_scope == reverse.normalized_scope
    assert canonicalize_qualifiers(canonicalize_qualifiers(left)) == canonicalize_qualifiers(left)


@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(size=st.integers(min_value=0, max_value=45), root_active=st.booleans())
def test_finite_derived_graph_reaches_a_deterministic_fixed_point(
    runtime, size: int, root_active: bool
) -> None:
    episode_id = new_id("episode")
    source = Source(
        id=new_id("source"),
        episode_id=episode_id,
        kind=SourceKind.TOOL,
        integrity=Integrity.TRUSTED,
        name="generated observer",
        root=f"generated:{episode_id}",
        competence={"general": 1.0},
        stats=SourceStats(),
    )
    observed_at = datetime(2026, 7, 11, tzinfo=UTC)
    root = Belief(
        id=new_id("belief"),
        episode_id=episode_id,
        content="Generated root holds",
        normalized_content="generated root holds",
        pramana=Pramana.PRATYAKSHA,
        source_id=source.id,
        evidence=(EvidenceRef(new_id("evidence")),),
        justifications=(),
        qualifiers={},
        perishability=Perishability.STABLE,
        observed_at=observed_at,
        stakes=Stakes.LOW,
        status=Status.IN,
        admission_status=Status.IN,
    )
    beliefs = {root.id: root}
    justifications: list[Justification] = []
    previous = root.id
    for index in range(size):
        belief_id = new_id("belief")
        justification = Justification(
            new_id("justification"), belief_id, (previous,), f"generated step {index}"
        )
        beliefs[belief_id] = Belief(
            id=belief_id,
            episode_id=episode_id,
            content=f"Generated conclusion {index} holds",
            normalized_content=f"generated conclusion {index} holds",
            pramana=Pramana.ANUMANA,
            source_id=source.id,
            evidence=(),
            justifications=(justification,),
            qualifiers={},
            perishability=Perishability.STABLE,
            observed_at=observed_at,
            stakes=Stakes.LOW,
            status=Status.IN,
            admission_status=Status.IN,
        )
        justifications.append(justification)
        previous = belief_id
    support = IngestionSupport(
        id=new_id("support"),
        episode_id=episode_id,
        belief_id=root.id,
        evidence_id=root.evidence[0].evidence_id,
        validity={},
        active=root_active,
    )
    first = relabel(
        beliefs,
        justifications,
        (support,),
        (),
        {source.id: source},
        runtime.config.data,
    )
    second = relabel(
        {
            belief_id: replace(belief, status=first.statuses[belief_id])
            for belief_id, belief in beliefs.items()
        },
        justifications,
        (support,),
        (),
        {source.id: source},
        runtime.config.data,
    )
    assert first.iterations <= size + 2
    assert second.statuses == first.statuses
    expected = Status.IN if root_active else Status.OUT
    assert set(first.statuses.values()) == {expected}


@settings(max_examples=24, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    result=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=0,
        max_size=1_500,
    )
)
def test_corrupted_unicode_tool_results_remain_bounded_and_replayable(runtime, result: str) -> None:
    service = runtime.begin_turn(
        session_id="property-session",
        turn_id=new_id("event"),
        user_message="Inspect an untrusted tool result.",
    )
    service.ingest_tool_result(
        "future_read_only_probe",
        {"query": "x"},
        result,
        session_id="property-session",
        turn_id=new_id("event"),
        tool_call_id=new_id("event"),
        status="unknown",
    )
    assert service.store.verify_hash_chain()[0]
    assert service.store.replay().deterministic
    evidence = service.store.events(service.episode_id)
    assert evidence
    assert (
        max(len(event.payload.get("record", {}).get("payload", "") or "") for event in evidence)
        <= 16_000
    )
