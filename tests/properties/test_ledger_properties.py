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


def test_mandatory_budget_entries_are_never_partially_rendered() -> None:
    budget = CharacterBudget(7)
    assert budget.add("abc")
    assert not budget.add("123456", mandatory=True)
    assert budget.render() == "abc"
    assert budget.used == 3
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


# deadline=None: this property asserts a deterministic fixed point, not latency. Each example
# drives real SQLite work through the function-scoped runtime, so under the gate's coverage
# instrumentation per-example wall time is dominated by machine load. A fixed deadline turns that
# into a flaky failure that says nothing about the property.
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
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


# deadline=None for the same reason as above: ingesting, verifying the hash chain, and replaying
# once per example is inherently variable, and the assertions below are about boundedness and
# replayability rather than speed.
@settings(
    max_examples=24,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
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


# Snapshotting at an arbitrary height must reproduce a full rebuild exactly. Generating the height
# rather than fixing it is the point: an off-by-one in the "replay only events above the snapshot"
# boundary would survive any single hand-chosen height.
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(cut=st.integers(min_value=0, max_value=40))
def test_a_snapshot_at_any_height_rebuilds_to_the_same_projections(tmp_path_factory, cut) -> None:
    import json
    from contextlib import closing
    from pathlib import Path

    from belief_ledger_pramana import snapshots
    from belief_ledger_pramana.events import (
        canonical_json,
        compute_event_auth,
        isoformat_utc,
        parse_datetime,
    )
    from belief_ledger_pramana.models import Event
    from belief_ledger_pramana.projections import apply_event
    from belief_ledger_pramana.store import LedgerStore

    fixture = (
        Path(__file__).parents[1] / "fixtures" / "v1_replay" / "contradiction_retraction.jsonl"
    )
    values = [
        json.loads(line)
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    database = tmp_path_factory.mktemp("snapshot-property") / "ledger.sqlite3"
    store = LedgerStore(database)

    height = min(cut, len(values))
    for index, value in enumerate(values, start=1):
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = Event(
                seq=int(value["seq"]),
                id=str(value["id"]),
                episode_id=str(value["episode_id"]),
                timestamp=parse_datetime(str(value["timestamp"])),
                kind=str(value["kind"]),
                schema_version=int(value["schema_version"]),
                aggregate_type=str(value["aggregate_type"]),
                aggregate_id=str(value["aggregate_id"]),
                correlation=dict(value["correlation"]),
                causal_event_id=value["causal_event_id"],
                payload=dict(value["payload"]),
                previous_hash=str(value["previous_hash"]),
                event_hash=str(value["event_hash"]),
            )
            connection.execute(
                "INSERT INTO events(seq,id,episode_id,ts,kind,schema_version,aggregate_type,"
                "aggregate_id,correlation_json,causal_event_id,payload_json,previous_hash,"
                "event_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.seq,
                    event.id,
                    event.episode_id,
                    isoformat_utc(event.timestamp),
                    event.kind,
                    event.schema_version,
                    event.aggregate_type,
                    event.aggregate_id,
                    canonical_json(event.correlation),
                    event.causal_event_id,
                    canonical_json(event.payload),
                    event.previous_hash,
                    event.event_hash,
                ),
            )
            connection.execute(
                "INSERT INTO event_auth(event_id,event_hash,auth_tag) VALUES (?,?,?)",
                (
                    event.id,
                    event.event_hash,
                    compute_event_auth(store._integrity_key, event.id, event.event_hash),
                ),
            )
            apply_event(connection, event)
            connection.commit()
        if index == height:
            # Capture at exactly this height, which is what makes the boundary interesting.
            with store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                snapshots.create(
                    connection,
                    scope="global",
                    chain_height=index,
                    created_at=datetime(2026, 8, 10, tzinfo=UTC),
                )
                connection.commit()

    store.replay()
    with closing(store.connect()) as connection:
        full = snapshots.projection_tables(connection)
    full_hash = store.projection_hash(version=2)

    store.replay(from_snapshot=True)
    with closing(store.connect()) as connection:
        accelerated = snapshots.projection_tables(connection)

    assert snapshots.first_difference(full, accelerated) is None
    assert store.projection_hash(version=2) == full_hash
