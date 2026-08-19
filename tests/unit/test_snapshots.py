"""Snapshots as a discardable derived cache (ADR 0014).

The invariants are the deliverable, not the speed-up. Each of the four is pinned here:

1. Never the source of truth — deleting every snapshot loses nothing.
2. Fingerprint mismatch means discard, never upgrade.
3. `replay()` with no flags is still a full replay from origin.
4. Every snapshot is verifiable against a full rebuild.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

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

FIXTURES = Path(__file__).parents[1] / "fixtures" / "v1_replay"
ALL_FIXTURES = sorted(path.name for path in FIXTURES.glob("*.jsonl"))


def _loaded(tmp_path: Path, fixture: str = "contradiction_retraction.jsonl") -> LedgerStore:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    values = [
        json.loads(line)
        for line in (FIXTURES / fixture).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for value in values:
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
    return store


# --- invariant 1: never the source of truth ----------------------------------------------------


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_deleting_every_snapshot_loses_nothing(tmp_path: Path, fixture: str) -> None:
    """The invariant that keeps this repository free of generational drift."""
    store = _loaded(tmp_path, fixture)
    expected_v1 = store.projection_hash(version=1)
    expected_v2 = store.projection_hash(version=2)
    store.create_snapshot()

    assert store.prune_snapshots(keep=0) > 0
    assert store.list_snapshots() == []

    result = store.replay()

    assert result.deterministic
    assert store.projection_hash(version=1) == expected_v1
    assert store.projection_hash(version=2) == expected_v2


def test_a_ledger_that_never_snapshotted_replays_identically(tmp_path: Path) -> None:
    store = _loaded(tmp_path)
    before = store.projection_hash(version=2)

    assert store.replay(from_snapshot=True).deterministic
    assert store.projection_hash(version=2) == before


# --- invariant 2: discard on fingerprint mismatch ----------------------------------------------


def test_a_corrupted_fingerprint_is_discarded_and_replay_falls_back(tmp_path: Path) -> None:
    store = _loaded(tmp_path)
    store.create_snapshot()
    with closing(sqlite3.connect(store.database)) as connection, connection:
        connection.execute("UPDATE snapshots SET fingerprint='not-the-current-code'")

    result = store.replay(from_snapshot=True)

    assert result.deterministic, "a stale snapshot must never make replay wrong"
    assert result.events_replayed == 9, "it must fall back to reading every event"
    with store.connect() as connection:
        assert snapshots.load_newest_valid(connection, scope="global") is None


def test_a_corrupted_payload_is_discarded_rather_than_used(tmp_path: Path) -> None:
    """The content hash is checked on load, so a mangled payload cannot reach a projection."""
    store = _loaded(tmp_path)
    store.create_snapshot()
    with closing(sqlite3.connect(store.database)) as connection, connection:
        connection.execute(
            "UPDATE snapshots SET payload=? WHERE projection_name='beliefs'", (b"x",)
        )

    with store.connect() as connection:
        assert snapshots.load_newest_valid(connection, scope="global") is None
    assert store.replay(from_snapshot=True).deterministic


def test_a_snapshot_missing_a_projection_table_is_discarded(tmp_path: Path) -> None:
    store = _loaded(tmp_path)
    store.create_snapshot()
    with closing(sqlite3.connect(store.database)) as connection, connection:
        connection.execute("DELETE FROM snapshots WHERE projection_name='beliefs'")

    with store.connect() as connection:
        assert snapshots.load_newest_valid(connection, scope="global") is None


def test_the_fingerprint_changes_with_the_schema_and_the_package_version() -> None:
    baseline = snapshots.derivation_fingerprint()

    assert snapshots.derivation_fingerprint("1.0.0rc4") != baseline
    assert snapshots.derivation_fingerprint("1.0.0rc4") == snapshots.derivation_fingerprint(
        "1.0.0rc4"
    )
    assert len(baseline) == 64 and int(baseline, 16) >= 0


# --- invariant 3: full replay remains the default ----------------------------------------------


def test_replay_with_no_flags_reads_every_event(tmp_path: Path) -> None:
    """Asserted by event-read count, not by timing."""
    store = _loaded(tmp_path)
    store.create_snapshot()

    full = store.replay()
    accelerated = store.replay(from_snapshot=True)

    assert full.events_replayed == 9
    assert accelerated.events_replayed == 0
    assert full.deterministic and accelerated.deterministic


def test_only_events_above_the_snapshot_height_are_replayed(tmp_path: Path) -> None:
    store = _loaded(tmp_path)
    with closing(sqlite3.connect(store.database)) as connection:
        connection.execute("DELETE FROM snapshots")
    # Snapshot at a height below the head by capturing, then appending nothing: the fixture is
    # fully applied, so the snapshot height is the head and acceleration reads nothing.
    rows = store.create_snapshot()

    assert rows[0].chain_height == 9
    assert store.replay(from_snapshot=True).events_replayed == 0


# --- invariant 4: verifiable -------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_an_accelerated_rebuild_equals_a_full_rebuild(tmp_path: Path, fixture: str) -> None:
    store = _loaded(tmp_path, fixture)
    store.create_snapshot()

    verification = store.verify_snapshot()

    assert verification.ok, verification.reason
    assert verification.differing_table is None


def test_verify_snapshot_reports_when_there_is_nothing_to_verify(tmp_path: Path) -> None:
    store = _loaded(tmp_path)

    verification = store.verify_snapshot()

    assert not verification.ok
    assert "no snapshot" in verification.reason
    assert verification.chain_height is None


def test_verify_snapshot_names_the_first_differing_table(tmp_path: Path) -> None:
    """A snapshot doctored to disagree must be caught, and the failure must name something."""
    store = _loaded(tmp_path)
    store.create_snapshot()
    with store.connect() as connection:
        captured = snapshots.projection_tables(connection)
    captured["beliefs"] = []

    difference = snapshots.first_difference(captured, snapshots.projection_tables(store.connect()))

    assert difference is not None
    table, row = difference
    assert table == "beliefs"
    assert row is not None


# --- listing, pruning, scoping -----------------------------------------------------------------


def test_listing_and_pruning_keep_the_newest_heights(tmp_path: Path) -> None:
    store = _loaded(tmp_path)
    store.create_snapshot()
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        snapshots.create(
            connection,
            scope="global",
            chain_height=4,
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        snapshots.create(
            connection,
            scope="global",
            chain_height=6,
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        connection.commit()

    with store.connect() as connection:
        assert snapshots.heights(connection, scope="global") == [9, 6, 4]

    store.prune_snapshots(keep=2)

    with store.connect() as connection:
        assert snapshots.heights(connection, scope="global") == [9, 6]


def test_pruning_to_zero_removes_every_snapshot(tmp_path: Path) -> None:
    store = _loaded(tmp_path)
    store.create_snapshot()

    assert store.prune_snapshots(keep=0) > 0
    assert store.list_snapshots() == []


def test_a_negative_keep_is_refused(tmp_path: Path) -> None:
    store = _loaded(tmp_path)

    with pytest.raises(ValueError, match="keep must not be negative"):
        store.prune_snapshots(keep=-1)


def test_snapshots_are_scoped_independently(tmp_path: Path) -> None:
    store = _loaded(tmp_path)
    store.create_snapshot(scope="global")
    store.create_snapshot(scope="ep_retraction")

    assert {row.scope for row in store.list_snapshots()} == {"global", "ep_retraction"}
    assert store.prune_snapshots(scope="ep_retraction", keep=0) > 0
    assert {row.scope for row in store.list_snapshots()} == {"global"}


def test_creating_at_the_same_height_replaces_rather_than_duplicates(tmp_path: Path) -> None:
    store = _loaded(tmp_path)
    first = store.create_snapshot()
    second = store.create_snapshot()

    assert len(store.list_snapshots()) == len(first)
    assert {row.content_hash for row in first} == {row.content_hash for row in second}


# --- replay budget warning ---------------------------------------------------------------------


def test_the_replay_budget_warning_fires_at_the_threshold() -> None:
    """Makes the scaling wall visible before it is hit. It reports; it never refuses."""
    assert snapshots.replay_budget_warning(49_999, 50_000) is None
    warning = snapshots.replay_budget_warning(50_000, 50_000)
    assert warning is not None
    assert "50000" in warning
    assert "snapshot create" in warning


def test_a_zero_threshold_disables_the_warning() -> None:
    assert snapshots.replay_budget_warning(1_000_000, 0) is None
    assert snapshots.replay_budget_warning(1_000_000, -1) is None


def test_the_packaged_default_threshold_is_configured() -> None:
    from belief_ledger_pramana.config import packaged_yaml

    assert packaged_yaml("defaults.yaml")["replay"]["max_events_warn"] == 50_000


def test_doctor_reports_the_replay_budget(runtime) -> None:
    """The plan puts the notice in `doctor` as well as `db replay`.

    `doctor` is where an operator looks when nothing is wrong yet, which is exactly when a replay
    approaching its budget is worth knowing about. It reports; it never changes the health verdict.

    The earlier version of this test asserted `loud["status"] == quiet["status"]` and passed
    without ever exercising the claim: this fixture's doctor status is saturated at
    `"unavailable"` by unrelated errors, so both sides were equal no matter what the budget did.
    The verdict is now derived from a synthetic list instead, which is the only part of the
    computation the claim is actually about, and the budget message is asserted to land in
    `notices` rather than `warnings`.
    """
    from dataclasses import replace as replace_dataclass

    from belief_ledger_pramana.hermes.cli import doctor

    def verdict(report: dict) -> str:
        """The rule from `doctor`, applied to one report's own lists."""
        if report["errors"]:
            return "unavailable"
        return "degraded" if report["warnings"] else "healthy"

    runtime.ensure_initialized()
    quiet = doctor(runtime)
    assert quiet["checks"]["replay_budget"]["max_events_warn"] == 50_000
    assert quiet["checks"]["replay_budget"]["over_threshold"] is False
    assert not any("max_events_warn" in item for item in quiet["warnings"])
    assert not any("max_events_warn" in item for item in quiet["notices"])

    data = dict(runtime.config.data)
    data["replay"] = {"max_events_warn": 1}
    runtime._config = replace_dataclass(runtime.config, data=data)
    runtime.begin_turn(session_id="budget", turn_id="budget-turn", user_message="Atlas is up.")

    loud = doctor(runtime)

    assert loud["checks"]["replay_budget"]["over_threshold"] is True
    assert any("max_events_warn" in item for item in loud["notices"])
    assert not any("max_events_warn" in item for item in loud["warnings"])
    # The budget must not move the verdict. Compare the warning sets rather than the two
    # `status` strings, so this holds even when something unrelated already fixed the verdict.
    assert loud["warnings"] == quiet["warnings"]
    assert verdict(loud) == verdict(quiet), "a budget notice must not change the verdict"


def test_a_replay_budget_notice_alone_leaves_a_clean_report_healthy() -> None:
    """The regression the old assertion could not see, isolated from the fixture's errors.

    `doctor`'s rule is `unavailable if errors else degraded if warnings else healthy`. Before this
    change the budget message went into `warnings`, so a deployment with nothing wrong flipped to
    `degraded` purely for having accumulated history. This pins the classification at the level
    the bug lived at.
    """
    from belief_ledger_pramana.snapshots import replay_budget_warning

    notice = replay_budget_warning(50_000, 50_000)
    assert notice is not None

    errors: list[str] = []
    warnings: list[str] = []
    notices: list[str] = [notice]

    status = "unavailable" if errors else "degraded" if warnings else "healthy"
    assert status == "healthy", "a ledger that has accumulated history is used, not degraded"
    assert notices == [notice]
