"""External anchoring of the hash chain (ADR 0013).

The acceptance test here is `test_a_rechained_tamper_passes_verify_chain_and_fails_anchor_verify`.
Everything else supports it. If that test can be made to pass without an anchor, anchoring is not
doing anything.
"""

from __future__ import annotations

import json
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from belief_ledger_pramana.events import (
    canonical_json,
    compute_event_auth,
    compute_event_hash,
    isoformat_utc,
    parse_datetime,
)
from belief_ledger_pramana.store import LedgerStore
from belief_ledger_pramana.verification.anchors import (
    GLOBAL_SCOPE,
    AnchorError,
    AnchorRecord,
    FileAnchorSink,
    build_record,
    compare_against_anchors,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "v1_replay"
CREATED = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _loaded_store(tmp_path: Path, fixture: str = "contradiction_retraction.jsonl") -> LedgerStore:
    """A ledger with real, chain-valid history, built from a frozen fixture."""
    store = LedgerStore(tmp_path / "ledger" / "ledger.sqlite3")
    events = [
        json.loads(line)
        for line in (FIXTURES / fixture).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for value in events:
            connection.execute(
                "INSERT INTO events(seq,id,episode_id,ts,kind,schema_version,aggregate_type,"
                "aggregate_id,correlation_json,causal_event_id,payload_json,previous_hash,"
                "event_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    int(value["seq"]),
                    value["id"],
                    value["episode_id"],
                    isoformat_utc(parse_datetime(value["timestamp"])),
                    value["kind"],
                    int(value["schema_version"]),
                    value["aggregate_type"],
                    value["aggregate_id"],
                    canonical_json(value["correlation"]),
                    value["causal_event_id"],
                    canonical_json(value["payload"]),
                    value["previous_hash"],
                    value["event_hash"],
                ),
            )
            connection.execute(
                "INSERT INTO event_auth(event_id,event_hash,auth_tag) VALUES (?,?,?)",
                (
                    value["id"],
                    value["event_hash"],
                    compute_event_auth(store._integrity_key, value["id"], value["event_hash"]),
                ),
            )
            connection.execute(
                "INSERT INTO event_heads(episode_id,seq,event_hash) VALUES (?,?,?) "
                "ON CONFLICT(episode_id) DO UPDATE SET seq=excluded.seq,"
                "event_hash=excluded.event_hash",
                (value["episode_id"], int(value["seq"]), value["event_hash"]),
            )
        connection.commit()
    return store


def _sink(tmp_path: Path, store: LedgerStore) -> FileAnchorSink:
    return FileAnchorSink(
        tmp_path / "anchors" / "chain-anchors.jsonl",
        ledger_directory=store.database.parent,
    )


def _publish(store: LedgerStore, sink: FileAnchorSink) -> AnchorRecord:
    record = build_record(
        store.chain_state(),
        ledger_id=str(store.database),
        scope=GLOBAL_SCOPE,
        created_at=CREATED,
        package_version="1.0.0rc4",
    )
    sink.publish(record)
    return record


def _verify(store: LedgerStore, sink: FileAnchorSink) -> list:
    current = store.chain_state()
    return compare_against_anchors(
        sink.fetch(),
        local_root_at=lambda height: store.chain_state(up_to_height=height).root_hash,
        current_height=current.chain_height,
    )


def _rechain_from(store: LedgerStore, mutated_seq: int) -> None:
    """Edit one event and re-chain everything after it, so `verify-chain` passes again.

    This is the attacker who holds the integrity key and can write the database file. The
    append-only triggers are dropped and restored, because that is exactly what such an attacker
    does — a trigger is a row in the schema of a file they can write. The re-chain is deliberately
    faithful rather than sloppy: a sloppy edit is caught by the existing chain check and would
    prove nothing about anchoring.
    """
    with closing(sqlite3.connect(store.database)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DROP TRIGGER IF EXISTS events_no_update")
        connection.execute("DROP TRIGGER IF EXISTS events_no_delete")
        rows = list(connection.execute("SELECT * FROM events ORDER BY seq"))
        heads: dict[str, str] = {}
        for row in rows:
            episode = str(row["episode_id"])
            previous = heads.get(episode, "0" * 64)
            payload = json.loads(str(row["payload_json"]))
            if int(row["seq"]) == mutated_seq:
                payload = {**payload, "tampered": True}
            body = {
                "seq": int(row["seq"]),
                "id": str(row["id"]),
                "episode_id": episode,
                "timestamp": parse_datetime(str(row["ts"])),
                "kind": str(row["kind"]),
                "schema_version": int(row["schema_version"]),
                "aggregate_type": str(row["aggregate_type"]),
                "aggregate_id": str(row["aggregate_id"]),
                "correlation": json.loads(str(row["correlation_json"])),
                "causal_event_id": row["causal_event_id"],
                "payload": payload,
                "previous_hash": previous,
            }
            digest = compute_event_hash(previous, body)
            connection.execute(
                "UPDATE events SET payload_json=?, previous_hash=?, event_hash=? WHERE seq=?",
                (canonical_json(payload), previous, digest, int(row["seq"])),
            )
            connection.execute(
                "UPDATE event_auth SET event_hash=?, auth_tag=? WHERE event_id=?",
                (
                    digest,
                    compute_event_auth(store._integrity_key, str(row["id"]), digest),
                    str(row["id"]),
                ),
            )
            connection.execute(
                "UPDATE event_heads SET seq=?, event_hash=? WHERE episode_id=?",
                (int(row["seq"]), digest, episode),
            )
            heads[episode] = digest
        connection.execute(
            "CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events "
            "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
        )
        connection.execute(
            "CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events "
            "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
        )
        connection.commit()


# --- the acceptance test -----------------------------------------------------------------------


def test_a_rechained_tamper_passes_verify_chain_and_fails_anchor_verify(tmp_path: Path) -> None:
    """The whole point of Stage 5, asserted end to end."""
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)
    anchored = _publish(store, sink)
    assert anchored.chain_height > 1
    assert store.verify_hash_chain()[0]
    assert all(item.ok for item in _verify(store, sink))

    _rechain_from(store, mutated_seq=2)

    # The attacker's work is complete and internally consistent: the chain still verifies.
    assert store.verify_hash_chain()[0], "the tamper must be invisible to the chain check alone"

    comparisons = _verify(store, sink)
    failures = [item for item in comparisons if not item.ok]
    assert failures, "anchor verification must catch what the chain check cannot"
    failure = failures[0]
    assert failure.status == "mismatch"
    assert failure.chain_height == anchored.chain_height
    assert failure.anchored_root == anchored.root_hash
    assert failure.local_root is not None
    assert failure.local_root != anchored.root_hash


def test_an_anchored_height_the_chain_no_longer_reaches_is_also_tamper_evidence(
    tmp_path: Path,
) -> None:
    """Deleting history is a different failure from rewriting it, and is reported as one."""
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)
    anchored = _publish(store, sink)

    with closing(sqlite3.connect(store.database)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DROP TRIGGER IF EXISTS events_no_update")
        connection.execute("DROP TRIGGER IF EXISTS events_no_delete")
        connection.execute("DELETE FROM events WHERE seq >= ?", (anchored.chain_height,))
        connection.execute("DELETE FROM event_heads")
        for row in connection.execute(
            "SELECT episode_id, MAX(seq) AS seq FROM events GROUP BY episode_id"
        ).fetchall():
            head = connection.execute(
                "SELECT event_hash FROM events WHERE seq=?", (int(row["seq"]),)
            ).fetchone()
            connection.execute(
                "INSERT INTO event_heads(episode_id,seq,event_hash) VALUES (?,?,?)",
                (str(row["episode_id"]), int(row["seq"]), str(head["event_hash"])),
            )
        connection.commit()

    failures = [item for item in _verify(store, sink) if not item.ok]

    assert [item.status for item in failures] == ["unreachable"]
    assert failures[0].local_root is None


def test_an_untampered_ledger_verifies_at_every_anchored_height(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)
    _publish(store, sink)
    _publish(store, sink)

    comparisons = _verify(store, sink)

    assert len(comparisons) == 2
    assert all(item.ok and item.status == "match" for item in comparisons)


# --- the sink ----------------------------------------------------------------------------------


def test_the_sink_is_append_only_and_never_rewrites(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)

    _publish(store, sink)
    first = sink.path.read_bytes()
    _publish(store, sink)
    second = sink.path.read_bytes()

    assert second.startswith(first), "an existing line must never be rewritten"
    assert len(second.splitlines()) == 2
    assert len(list(sink.fetch())) == 2


def test_the_sink_file_is_private_to_its_owner(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)

    _publish(store, sink)

    assert stat.S_IMODE(sink.path.stat().st_mode) & 0o077 == 0


def test_a_sink_inside_the_ledger_directory_is_refused(tmp_path: Path) -> None:
    """A sink the ledger's own attacker already owns is not an external anchor."""
    store = _loaded_store(tmp_path)

    with pytest.raises(AnchorError, match="outside the ledger directory"):
        FileAnchorSink(
            store.database.parent / "anchors.jsonl", ledger_directory=store.database.parent
        )
    with pytest.raises(AnchorError, match="outside the ledger directory"):
        FileAnchorSink(
            store.database.parent / "nested" / "anchors.jsonl",
            ledger_directory=store.database.parent,
        )


def test_a_sink_outside_the_ledger_directory_is_accepted(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)

    sink = FileAnchorSink(
        tmp_path / "elsewhere" / "anchors.jsonl", ledger_directory=store.database.parent
    )

    assert sink.path.parent.is_dir()


def test_no_credential_or_secret_material_reaches_a_published_record(tmp_path: Path) -> None:
    """Anchors carry digests and metadata. Nothing else, and in particular not the HMAC key."""
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)

    record = _publish(store, sink)

    assert set(record.as_json()) == {
        "chain_height",
        "created_at",
        "hash_algorithm",
        "ledger_id",
        "package_version",
        "record_version",
        "root_hash",
        "scope",
    }
    written = sink.path.read_bytes()
    assert store._integrity_key not in written
    assert store._integrity_key.hex().encode() not in written


def test_a_malformed_line_is_reported_with_its_position(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)
    _publish(store, sink)
    with sink.path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    with pytest.raises(AnchorError, match=r":2: not valid JSON"):
        list(sink.fetch())


def test_fetch_can_start_from_a_height(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)
    record = _publish(store, sink)

    assert len(list(sink.fetch(since_height=record.chain_height))) == 1
    assert list(sink.fetch(since_height=record.chain_height + 1)) == []


def test_fetch_on_a_missing_file_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)

    sink = _sink(tmp_path, store)

    assert list(sink.fetch()) == []


# --- chain state -------------------------------------------------------------------------------


def test_chain_state_at_a_height_is_the_state_that_height_had(tmp_path: Path) -> None:
    """This is what makes an anchor checkable after more events were appended."""
    store = _loaded_store(tmp_path)
    full = store.chain_state()
    early = store.chain_state(up_to_height=2)

    assert early.chain_height == 2
    assert early.root_hash != full.root_hash
    assert store.chain_state(up_to_height=2).root_hash == early.root_hash
    assert store.chain_state(up_to_height=full.chain_height).root_hash == full.root_hash


def test_chain_state_shares_its_verification_with_verify_hash_chain(tmp_path: Path) -> None:
    """One head computation, so a mismatch is never ambiguous between tampering and a bug."""
    store = _loaded_store(tmp_path)

    ok, heads_json = store.verify_hash_chain()
    state = store.chain_state()

    assert ok
    assert json.loads(heads_json) == {episode: list(head) for episode, head in state.heads.items()}
    assert state.hash_algorithm == "sha256-canonical-json-heads"


def test_an_empty_ledger_anchors_without_special_casing(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger" / "empty.sqlite3")
    sink = _sink(tmp_path, store)

    record = _publish(store, sink)

    assert record.chain_height == 0
    assert all(item.ok for item in _verify(store, sink))


def test_a_record_round_trips_through_json(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    sink = _sink(tmp_path, store)
    original = _publish(store, sink)

    assert next(iter(sink.fetch())) == original


def test_a_record_missing_a_required_field_is_rejected() -> None:
    with pytest.raises(AnchorError, match="malformed anchor record"):
        AnchorRecord.from_json({"scope": "global"})
    with pytest.raises(AnchorError, match="must be an object"):
        AnchorRecord.from_json(["global"])


def test_sqlite_tampering_helper_actually_produces_a_valid_chain(tmp_path: Path) -> None:
    """A guard on the test itself: if the helper left the chain broken, the acceptance test
    would pass for the wrong reason."""
    store = _loaded_store(tmp_path)
    _rechain_from(store, mutated_seq=2)

    ok, _ = store.verify_hash_chain()

    assert ok
    with closing(sqlite3.connect(store.database)) as connection, connection:
        payload = connection.execute("SELECT payload_json FROM events WHERE seq=2").fetchone()[0]
    assert '"tampered":true' in payload
