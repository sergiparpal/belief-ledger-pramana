from __future__ import annotations

import concurrent.futures
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.models import CompatibilityMode, Episode, Stakes
from belief_ledger_pramana.store import EventDraft, LedgerStore


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
