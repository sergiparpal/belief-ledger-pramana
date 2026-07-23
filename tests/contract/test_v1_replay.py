from __future__ import annotations

import json
from pathlib import Path

import pytest

from belief_ledger_pramana.compatibility import CompatibilityReport
from belief_ledger_pramana.events import (
    canonical_json,
    compute_event_auth,
    isoformat_utc,
    parse_datetime,
)
from belief_ledger_pramana.models import CompatibilityMode, Event
from belief_ledger_pramana.projections import apply_event
from belief_ledger_pramana.store import LedgerStore

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _event(value: dict[str, object]) -> Event:
    return Event(
        seq=int(value["seq"]),
        id=str(value["id"]),
        episode_id=str(value["episode_id"]),
        timestamp=parse_datetime(str(value["timestamp"])),
        kind=str(value["kind"]),
        schema_version=int(value["schema_version"]),
        aggregate_type=str(value["aggregate_type"]),
        aggregate_id=str(value["aggregate_id"]),
        correlation={str(key): str(item) for key, item in dict(value["correlation"]).items()},
        causal_event_id=(
            str(value["causal_event_id"]) if value["causal_event_id"] is not None else None
        ),
        payload=dict(value["payload"]),
        previous_hash=str(value["previous_hash"]),
        event_hash=str(value["event_hash"]),
    )


def _load_fixture(store: LedgerStore, fixture: Path) -> None:
    events = [
        _event(json.loads(line))
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for event in events:
            connection.execute(
                "INSERT INTO events(seq,id,episode_id,ts,kind,schema_version,aggregate_type,"
                "aggregate_id,correlation_json,causal_event_id,payload_json,previous_hash,event_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
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


@pytest.mark.parametrize(
    "name,expected",
    sorted(
        json.loads((FIXTURES / "v1_replay" / "manifest.json").read_text(encoding="utf-8"))[
            "fixtures"
        ].items()
    ),
)
def test_v1_fixture_replays_with_frozen_projection_hash(
    tmp_path: Path, name: str, expected: str
) -> None:
    store = LedgerStore(tmp_path / name.replace(".jsonl", ".sqlite3"))
    _load_fixture(store, FIXTURES / "v1_replay" / name)
    assert store.verify_hash_chain()[0]
    assert store.projection_hash(version=1) == expected
    assert store.replay().deterministic
    assert store.projection_hash(version=1) == expected


def test_empty_future_projection_cannot_change_v1_hash(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "future.sqlite3")
    before = store.projection_hash(version=1)
    with store.connect() as connection:
        connection.execute("CREATE TABLE future_projection(id TEXT PRIMARY KEY)")
    assert store.projection_hash(version=1) == before


@pytest.mark.parametrize(
    "mode,capabilities,errors,warnings,name",
    [
        (
            CompatibilityMode.FULL,
            {
                key: True
                for key in (
                    "register_tool",
                    "register_hook",
                    "register_middleware",
                    "register_command",
                    "register_cli_command",
                    "llm_facade",
                )
            },
            (),
            (),
            "full",
        ),
        (
            CompatibilityMode.HOOK_CONTEXT,
            {
                "register_tool": True,
                "register_hook": True,
                "register_middleware": False,
                "register_command": True,
                "register_cli_command": True,
                "llm_facade": True,
            },
            (),
            (
                "per-request context injection is unavailable; compatibility context is per turn",
                "strict enforcement is not claimed in this compatibility mode",
            ),
            "hook_context",
        ),
        (
            CompatibilityMode.DIAGNOSTICS_ONLY,
            {
                key: False
                for key in (
                    "register_tool",
                    "register_hook",
                    "register_middleware",
                    "register_command",
                    "register_cli_command",
                    "llm_facade",
                )
            },
            (
                "missing Hermes capabilities: llm_facade, register_cli_command, register_command, register_hook, register_middleware, register_tool",
            ),
            ("strict enforcement is not claimed in this compatibility mode",),
            "diagnostics_only",
        ),
    ],
)
def test_normalized_compatibility_snapshots_are_stable(
    mode: CompatibilityMode,
    capabilities: dict[str, bool],
    errors: tuple[str, ...],
    warnings: tuple[str, ...],
    name: str,
) -> None:
    report = CompatibilityReport(mode, "0.18.2", "ignored", capabilities, errors, warnings)
    expected = json.loads((FIXTURES / "compatibility" / f"{name}.json").read_text())
    assert report.normalized_snapshot() == expected
