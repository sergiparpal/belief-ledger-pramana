from __future__ import annotations

import concurrent.futures
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.models import CompatibilityMode, Episode, Stakes
from belief_ledger_pramana.store import EventDraft, LedgerStore, LlmReservationError


def _episode() -> Episode:
    now = datetime.now(UTC)
    return Episode(
        id=new_id("episode"),
        key=f"session:{new_id('episode')}",
        session_id="session",
        task_id="",
        platform="test",
        model="scripted",
        default_stakes=Stakes.MED,
        current_turn=0,
        created_at=now,
        updated_at=now,
        compatibility_mode=CompatibilityMode.FULL,
    )


def test_events_are_immutable_and_replay_exact(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    episode = _episode()
    store.create_episode(episode)
    store.append_events(
        episode.id,
        [EventDraft("NOTE", "episode", episode.id, {"value": 1})],
    )
    before = store.projection_hash()
    replay = store.replay()
    assert replay.before_hash == replay.after_hash == before
    assert store.verify_hash_chain()[0]
    with store.connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE events SET kind='TAMPERED' WHERE seq=1")
    with store.connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM events WHERE seq=1")


def test_idempotency_deduplicates_parallel_callbacks(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    episode = _episode()
    store.create_episode(episode)

    def append() -> tuple[str, ...]:
        return tuple(
            event.id
            for event in store.append_events(
                episode.id,
                [EventDraft("CALLBACK", "episode", episode.id, {"value": 1})],
                idempotency_key="same-callback",
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: append(), range(20)))
    assert len({result for result in results}) == 1
    assert [event.kind for event in store.events(episode.id)].count("CALLBACK") == 1


def test_idempotency_replays_the_entire_batch_and_is_episode_scoped(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    first = _episode()
    second = _episode()
    store.create_episode(first)
    store.create_episode(second)
    drafts = [
        EventDraft("FIRST", "episode", first.id, {"value": 1}),
        EventDraft("SECOND", "episode", first.id, {"value": 2}),
    ]
    appended = store.append_events(first.id, drafts, idempotency_key="same-key")
    repeated = store.append_events(first.id, drafts, idempotency_key="same-key")
    assert [event.id for event in repeated] == [event.id for event in appended]
    other = store.append_events(
        second.id,
        [EventDraft("OTHER", "episode", second.id, {"value": 3})],
        idempotency_key="same-key",
    )
    assert len(other) == 1
    assert store.replay().deterministic


def test_llm_budget_reservations_are_atomic(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    episode = _episode()
    store.create_episode(episode)

    def reserve() -> str | None:
        try:
            return store.reserve_llm_budget(
                episode.id,
                0,
                input_tokens=10,
                output_tokens=10,
                max_calls_turn=1,
                max_calls_episode=1,
                max_input_tokens_episode=100,
                max_output_tokens_episode=100,
            )
        except LlmReservationError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        reservations = list(pool.map(lambda _: reserve(), range(4)))
    accepted = [reservation for reservation in reservations if reservation]
    assert len(accepted) == 1
    store.release_llm_reservation(accepted[0])


def test_v1_database_migrates_with_online_backup(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = LedgerStore(database)
    store.create_episode(_episode())
    with store.connect() as connection:
        connection.execute("DROP TABLE llm_reservations")
        connection.execute("DELETE FROM schema_migrations WHERE version=2")
    migrated = LedgerStore(database)
    assert migrated.migration.from_version == 1
    assert migrated.migration.to_version == 2
    assert migrated.migration.backup is not None and migrated.migration.backup.exists()
    with migrated.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_reservations'"
        ).fetchone()


def test_hash_chain_detects_payload_mutation_even_if_trigger_removed(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    episode = _episode()
    store.create_episode(episode)
    with store.connect() as connection:
        connection.execute("DROP TRIGGER events_no_update")
        connection.execute("UPDATE events SET payload_json='{}' WHERE seq=1")
    with pytest.raises(Exception, match="hash mismatch"):
        store.verify_hash_chain()


def test_confirmed_offline_purge_rewrites_only_other_episodes(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    first = _episode()
    second = _episode()
    store.create_episode(first)
    store.create_episode(second)
    store.append_events(first.id, [EventDraft("PRIVATE", "episode", first.id, {"secret": 1})])
    store.append_events(second.id, [EventDraft("KEEP", "episode", second.id, {"value": 2})])
    with pytest.raises(ValueError, match="confirmation"):
        store.purge_episode(first.id, confirmation=second.id)
    result = store.purge_episode(first.id, confirmation=first.id)
    assert result.events_removed == 2
    assert store.get_episode(first.id) is None
    assert store.get_episode(second.id) is not None
    assert all(event.episode_id == second.id for event in store.events())
    assert store.replay().deterministic
