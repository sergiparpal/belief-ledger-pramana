from __future__ import annotations

from datetime import UTC, datetime

from belief_ledger_pramana.events import build_event, canonical_json, compute_event_hash
from belief_ledger_pramana.ids import is_typed_id, new_id


def test_all_public_id_prefixes_are_typed_and_safe() -> None:
    for kind in (
        "event",
        "episode",
        "evidence",
        "belief",
        "justification",
        "defeat",
        "verification",
        "source",
        "retraction",
    ):
        value = new_id(kind)
        assert is_typed_id(value, kind)
        assert "/" not in value


def test_event_hash_commits_to_payload_and_previous_hash() -> None:
    event = build_event(
        seq=1,
        episode_id=new_id("episode"),
        kind="TEST",
        aggregate_type="test",
        aggregate_id="x",
        payload={"z": 2, "a": 1},
        previous_hash="0" * 64,
        timestamp=datetime(2026, 7, 11, tzinfo=UTC),
        event_id=new_id("event"),
    )
    body = {
        "seq": event.seq,
        "id": event.id,
        "episode_id": event.episode_id,
        "timestamp": event.timestamp,
        "kind": event.kind,
        "schema_version": event.schema_version,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "correlation": event.correlation,
        "causal_event_id": event.causal_event_id,
        "payload": event.payload,
        "previous_hash": event.previous_hash,
    }
    assert event.event_hash == compute_event_hash(event.previous_hash, body)
    assert canonical_json({"z": 2, "a": 1}) == '{"a":1,"z":2}'
