"""Append-only SQLite event store and deterministic projections."""

from __future__ import annotations

import hmac
import json
import os
import random
import re
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, TypeVar

from .events import (
    build_event,
    canonical_json,
    compute_event_auth,
    compute_event_hash,
    isoformat_utc,
    parse_datetime,
    to_primitive,
    utc_now,
)
from .ids import new_id
from .integrity import load_or_create_integrity_key
from .migrations import PROJECTION_TABLES, MigrationResult, configure_connection, migrate
from .models import (
    Belief,
    ChainAudit,
    CompatibilityMode,
    Conflict,
    DefeatEdge,
    DefeatKind,
    Episode,
    Event,
    Evidence,
    EvidenceRef,
    IngestionSupport,
    Integrity,
    Justification,
    Perishability,
    Pramana,
    RetractionNotice,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    Status,
    VerificationMethod,
    VerificationTask,
)
from .projections import apply_event

ZERO_HASH = "0" * 64
_T = TypeVar("_T")


class StoreError(RuntimeError):
    pass


class HashChainError(StoreError):
    pass


class LlmReservationError(StoreError):
    pass


class _ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager also releases the handle."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@dataclass(frozen=True, slots=True)
class EventDraft:
    kind: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    causal_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    events_replayed: int
    before_hash: str
    after_hash: str

    @property
    def deterministic(self) -> bool:
        return self.before_hash == self.after_hash


@dataclass(frozen=True, slots=True)
class PurgeResult:
    episode_id: str
    events_removed: int
    events_preserved: int
    post_purge_projection_hash: str


class LedgerStore:
    """A connection-per-operation event store safe for threaded Hermes callbacks."""

    def __init__(
        self,
        database: Path,
        *,
        busy_timeout_ms: int = 5_000,
        integrity_key_path: Path | None = None,
    ) -> None:
        requested_database = database.absolute()
        if requested_database.is_symlink():
            raise StoreError("ledger database must not be a symbolic link")
        self.database = requested_database.resolve()
        self.busy_timeout_ms = busy_timeout_ms
        requested_key = (
            integrity_key_path.absolute()
            if integrity_key_path is not None
            else self.database.with_name(f".{self.database.name}.integrity.key")
        )
        if requested_key.is_symlink():
            raise StoreError("ledger integrity key must not be a symbolic link")
        self.integrity_key_path = requested_key
        self._integrity_key = load_or_create_integrity_key(self.integrity_key_path)
        self.migration: MigrationResult = migrate(
            self.database,
            self._integrity_key,
            busy_timeout_ms,
        )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
            check_same_thread=False,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        configure_connection(connection, self.busy_timeout_ms)
        return connection

    def _run_immediate_transaction(
        self,
        operation: Callable[[sqlite3.Connection], _T],
        *,
        error_type: type[StoreError],
    ) -> _T:
        """Run one transaction with the store's bounded busy-retry policy."""

        deadline = time.monotonic() + self.busy_timeout_ms / 1_000
        attempt = 0
        while True:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                result = operation(connection)
                connection.commit()
                return result
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if not _is_busy(exc) or time.monotonic() >= deadline:
                    raise error_type(str(exc)) from exc
                attempt += 1
                time.sleep(min(0.05, 0.002 * (2 ** min(attempt, 5))) + random.random() * 0.003)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def append_events(
        self,
        episode_id: str,
        drafts: Sequence[EventDraft],
        *,
        correlation: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        require_open_verification_task_id: str | None = None,
    ) -> list[Event]:
        """Append a batch and update projections in one immediate transaction."""

        if not drafts:
            return []
        clean_correlation = {
            str(key): str(value)
            for key, value in (correlation or {}).items()
            if value is not None and str(value)
        }
        if idempotency_key:
            clean_correlation["idempotency_key"] = idempotency_key
        storage_idempotency_key = (
            _idempotency_storage_key(episode_id, idempotency_key) if idempotency_key else None
        )

        def append(connection: sqlite3.Connection) -> list[Event]:
            if require_open_verification_task_id:
                task = connection.execute(
                    "SELECT state FROM verification_tasks WHERE id=? AND episode_id=?",
                    (require_open_verification_task_id, episode_id),
                ).fetchone()
                if task is None or str(task["state"]) != "open":
                    return []
            if idempotency_key:
                existing = connection.execute(
                    "SELECT event_ids_json FROM idempotency "
                    "WHERE episode_id=? AND idempotency_key IN (?,?)",
                    (episode_id, storage_idempotency_key, idempotency_key),
                ).fetchone()
                if existing:
                    return self._events_by_ids(connection, json.loads(str(existing[0])))

            head = connection.execute(
                "SELECT seq,event_hash FROM event_heads WHERE episode_id=?", (episode_id,)
            ).fetchone()
            previous_hash = str(head["event_hash"]) if head else ZERO_HASH
            next_seq = int(
                connection.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events").fetchone()[0]
            )
            events: list[Event] = []
            for offset, draft in enumerate(drafts):
                event = build_event(
                    seq=next_seq + offset,
                    episode_id=episode_id,
                    kind=draft.kind,
                    aggregate_type=draft.aggregate_type,
                    aggregate_id=draft.aggregate_id,
                    payload=to_primitive(draft.payload),
                    previous_hash=previous_hash,
                    correlation=clean_correlation,
                    causal_event_id=draft.causal_event_id,
                )
                event = replace(
                    event,
                    auth_tag=compute_event_auth(self._integrity_key, event.id, event.event_hash),
                )
                _insert_event(connection, event)
                apply_event(connection, event)
                events.append(event)
                previous_hash = event.event_hash
            if storage_idempotency_key:
                connection.execute(
                    "INSERT INTO idempotency(idempotency_key,episode_id,event_ids_json,created_at) "
                    "VALUES (?,?,?,?)",
                    (
                        storage_idempotency_key,
                        episode_id,
                        canonical_json([event.id for event in events]),
                        isoformat_utc(events[0].timestamp),
                    ),
                )
            return events

        return self._run_immediate_transaction(append, error_type=StoreError)

    def append_record(
        self,
        episode_id: str,
        *,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        record: Any,
        correlation: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> Event:
        events = self.append_events(
            episode_id,
            [EventDraft(kind, aggregate_type, aggregate_id, {"record": to_primitive(record)})],
            correlation=correlation,
            idempotency_key=idempotency_key,
        )
        return events[0]

    def create_episode(self, episode: Episode, correlation: dict[str, str] | None = None) -> Event:
        return self.append_record(
            episode.id,
            kind="EPISODE_CREATED",
            aggregate_type="episode",
            aggregate_id=episode.id,
            record=episode,
            correlation=correlation,
        )

    def get_episode(self, episode_id: str) -> Episode | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        return _episode_from_row(row) if row else None

    def get_episode_by_key(self, key: str) -> Episode | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE episode_key=?", (key,)
            ).fetchone()
        return _episode_from_row(row) if row else None

    def list_episodes(self, limit: int = 100) -> list[Episode]:
        limit = max(1, min(limit, 1_000))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM episodes ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_episode_from_row(row) for row in rows]

    def get_source(self, source_id: str) -> Source | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return _source_from_row(row) if row else None

    def get_sources(self, source_ids: Iterable[str]) -> dict[str, Source]:
        """Return sources by ID without opening one connection per source."""

        ids = sorted({str(source_id) for source_id in source_ids if str(source_id)})
        if not ids:
            return {}
        rows: list[sqlite3.Row] = []
        with self.connect() as connection:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    connection.execute(
                        f"SELECT * FROM sources WHERE id IN ({placeholders})", chunk
                    ).fetchall()
                )
        return {source.id: source for source in (_source_from_row(row) for row in rows)}

    def find_source(self, episode_id: str, root: str, kind: SourceKind) -> Source | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE episode_id=? AND root=? AND kind=?",
                (episode_id, root, kind.value),
            ).fetchone()
        return _source_from_row(row) if row else None

    def list_sources(self, episode_id: str) -> list[Source]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sources WHERE episode_id=? ORDER BY id", (episode_id,)
            ).fetchall()
        return [_source_from_row(row) for row in rows]

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        return _evidence_from_row(row) if row else None

    def get_evidence_many(self, evidence_ids: Iterable[str]) -> dict[str, Evidence]:
        """Return evidence by ID in bounded `IN` queries."""

        ids = sorted({str(evidence_id) for evidence_id in evidence_ids if str(evidence_id)})
        if not ids:
            return {}
        rows: list[sqlite3.Row] = []
        with self.connect() as connection:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    connection.execute(
                        f"SELECT * FROM evidence WHERE id IN ({placeholders})", chunk
                    ).fetchall()
                )
        return {evidence.id: evidence for evidence in (_evidence_from_row(row) for row in rows)}

    def get_belief(self, belief_id: str) -> Belief | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM beliefs WHERE id=?", (belief_id,)).fetchone()
            if not row:
                return None
            return _hydrate_beliefs(connection, [row])[0]

    def get_beliefs(self, belief_ids: Iterable[str]) -> dict[str, Belief]:
        """Return a hydrated belief map without N+1 primary-key lookups."""

        ids = sorted({str(belief_id) for belief_id in belief_ids if str(belief_id)})
        if not ids:
            return {}
        rows: list[sqlite3.Row] = []
        with self.connect() as connection:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    connection.execute(
                        f"SELECT * FROM beliefs WHERE id IN ({placeholders})", chunk
                    ).fetchall()
                )
            beliefs = _hydrate_beliefs(connection, rows)
        return {belief.id: belief for belief in beliefs}

    def list_beliefs(
        self,
        episode_id: str,
        *,
        statuses: Iterable[Status] | None = None,
        pramanas: Iterable[Pramana] | None = None,
        limit: int = 5_000,
    ) -> list[Belief]:
        clauses = ["episode_id=?"]
        params: list[Any] = [episode_id]
        status_values = [item.value for item in statuses or ()]
        if status_values:
            clauses.append(f"status IN ({','.join('?' for _ in status_values)})")
            params.extend(status_values)
        type_values = [item.value for item in pramanas or ()]
        if type_values:
            clauses.append(f"pramana IN ({','.join('?' for _ in type_values)})")
            params.extend(type_values)
        params.append(max(1, min(limit, 20_000)))
        query = (
            f"SELECT * FROM beliefs WHERE {' AND '.join(clauses)} ORDER BY observed_at,id LIMIT ?"
        )
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            return _hydrate_beliefs(connection, rows)

    def find_exact_beliefs(self, episode_id: str, normalized_content: str) -> list[Belief]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM beliefs WHERE episode_id=? AND normalized_content=? ORDER BY id",
                (episode_id, normalized_content),
            ).fetchall()
            return _hydrate_beliefs(connection, rows)

    def list_justifications(self, episode_id: str) -> list[Justification]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,belief_id,warrant,audit_json,alternatives_json FROM justifications WHERE episode_id=? ORDER BY id",
                (episode_id,),
            ).fetchall()
            return _hydrate_justifications(connection, rows)

    def list_supports(self, episode_id: str) -> list[IngestionSupport]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ingestion_supports WHERE episode_id=? ORDER BY id", (episode_id,)
            ).fetchall()
        return [
            IngestionSupport(
                id=str(row["id"]),
                episode_id=str(row["episode_id"]),
                belief_id=str(row["belief_id"]),
                evidence_id=str(row["evidence_id"]),
                validity=json.loads(str(row["validity_json"])),
                active=bool(row["active"]),
            )
            for row in rows
        ]

    def list_defeats(self, episode_id: str) -> list[DefeatEdge]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM defeats WHERE episode_id=? ORDER BY id", (episode_id,)
            ).fetchall()
        return [
            DefeatEdge(
                id=str(row["id"]),
                episode_id=str(row["episode_id"]),
                attacker=str(row["attacker"]),
                target=str(row["target"]),
                kind=DefeatKind(str(row["kind"])),
                basis=str(row["basis"]),
                active=bool(row["active"]),
            )
            for row in rows
        ]

    def list_verification_tasks(
        self, episode_id: str, *, state: str | None = None
    ) -> list[VerificationTask]:
        query = "SELECT * FROM verification_tasks WHERE episode_id=?"
        params: list[Any] = [episode_id]
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY id"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_verification_from_row(row) for row in rows]

    def get_verification_task(self, task_id: str) -> VerificationTask | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM verification_tasks WHERE id=?", (task_id,)
            ).fetchone()
        return _verification_from_row(row) if row else None

    def list_conflicts(self, episode_id: str, *, state: str | None = "open") -> list[Conflict]:
        query = "SELECT * FROM conflicts WHERE episode_id=?"
        params: list[Any] = [episode_id]
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY id"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            Conflict(
                id=str(row["id"]),
                episode_id=str(row["episode_id"]),
                left_belief_id=str(row["left_belief_id"]),
                right_belief_id=str(row["right_belief_id"]),
                normalized_scope=json.loads(str(row["normalized_scope_json"])),
                verification_task_id=str(row["verification_task_id"]),
                state=str(row["state"]),
            )
            for row in rows
        ]

    def list_retractions(
        self, episode_id: str, *, state: str | None = "active"
    ) -> list[RetractionNotice]:
        query = "SELECT * FROM retraction_notices WHERE episode_id=?"
        params: list[Any] = [episode_id]
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY created_turn,id"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            RetractionNotice(
                id=str(row["id"]),
                episode_id=str(row["episode_id"]),
                defeated_belief_id=str(row["defeated_belief_id"]),
                cause=str(row["cause"]),
                descendants=tuple(json.loads(str(row["descendants_json"]))),
                created_turn=int(row["created_turn"]),
                ttl_turns=int(row["ttl_turns"]),
                state=str(row["state"]),
            )
            for row in rows
        ]

    def was_rendered(self, episode_id: str, belief_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM rendered_beliefs WHERE episode_id=? AND belief_id=? LIMIT 1",
                (episode_id, belief_id),
            ).fetchone()
        return row is not None

    def rendered_belief_ids(self, episode_id: str, belief_ids: Iterable[str]) -> set[str]:
        """Return rendered IDs for a batch of transition candidates."""

        ids = sorted({str(belief_id) for belief_id in belief_ids if str(belief_id)})
        if not ids:
            return set()
        rendered: set[str] = set()
        with self.connect() as connection:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT belief_id FROM rendered_beliefs "
                    f"WHERE episode_id=? AND belief_id IN ({placeholders})",
                    [episode_id, *chunk],
                ).fetchall()
                rendered.update(str(row[0]) for row in rows)
        return rendered

    def list_unpromoted(self, episode_id: str, *, limit: int = 100) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT evidence_id,source_profile,state,reason FROM unpromoted_evidence "
                "WHERE episode_id=? AND state='open' ORDER BY evidence_id LIMIT ?",
                (episode_id, max(1, min(limit, 1_000))),
            ).fetchall()
        return [
            {
                "evidence_id": str(row["evidence_id"]),
                "source_profile": str(row["source_profile"]),
                "state": str(row["state"]),
                "reason": str(row["reason"]),
            }
            for row in rows
        ]

    def is_unpromoted(self, episode_id: str, evidence_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM unpromoted_evidence "
                "WHERE episode_id=? AND evidence_id=? AND state='open'",
                (episode_id, evidence_id),
            ).fetchone()
        return row is not None

    def latest_assistant_response(self, episode_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT content FROM assistant_responses WHERE episode_id=? ORDER BY rowid DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def llm_usage_count(self, episode_id: str, turn_number: int) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM llm_usage WHERE episode_id=? AND turn_number=?",
                (episode_id, turn_number),
            ).fetchone()
        return int(row[0]) if row else 0

    def reserve_llm_budget(
        self,
        episode_id: str,
        turn_number: int,
        *,
        input_tokens: int,
        output_tokens: int,
        max_calls_turn: int,
        max_calls_episode: int,
        max_input_tokens_episode: int,
        max_output_tokens_episode: int,
        stale_after_seconds: int = 300,
    ) -> str:
        """Atomically reserve one component-call budget across callbacks/processes."""

        if min(input_tokens, output_tokens, max_calls_turn, max_calls_episode) < 0:
            raise LlmReservationError("LLM budget values must be non-negative")
        reservation_id = new_id("reservation")

        def reserve(connection: sqlite3.Connection) -> str:
            now = utc_now()
            cutoff = isoformat_utc(now - timedelta(seconds=max(1, stale_after_seconds)))
            connection.execute("DELETE FROM llm_reservations WHERE created_at<?", (cutoff,))
            episode = connection.execute(
                "SELECT llm_calls_used,input_tokens_used,output_tokens_used FROM episodes WHERE id=?",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise LlmReservationError("episode does not exist")
            reserved = connection.execute(
                "SELECT COUNT(*) AS calls,COALESCE(SUM(input_tokens),0) AS input_tokens,"
                "COALESCE(SUM(output_tokens),0) AS output_tokens "
                "FROM llm_reservations WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            turn_calls = connection.execute(
                "SELECT COUNT(*) FROM llm_reservations WHERE episode_id=? AND turn_number=?",
                (episode_id, turn_number),
            ).fetchone()
            used_turn = connection.execute(
                "SELECT COUNT(*) FROM llm_usage WHERE episode_id=? AND turn_number=?",
                (episode_id, turn_number),
            ).fetchone()
            if int(used_turn[0]) + int(turn_calls[0]) >= max_calls_turn:
                raise LlmReservationError("turn LLM call budget exhausted")
            if int(episode["llm_calls_used"]) + int(reserved["calls"]) >= max_calls_episode:
                raise LlmReservationError("episode LLM call budget exhausted")
            if (
                int(episode["input_tokens_used"]) + int(reserved["input_tokens"]) + input_tokens
                > max_input_tokens_episode
            ):
                raise LlmReservationError("episode input-token budget exhausted")
            if (
                int(episode["output_tokens_used"]) + int(reserved["output_tokens"]) + output_tokens
                > max_output_tokens_episode
            ):
                raise LlmReservationError("episode output-token budget exhausted")
            connection.execute(
                "INSERT INTO llm_reservations(id,episode_id,turn_number,input_tokens,output_tokens,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    reservation_id,
                    episode_id,
                    turn_number,
                    input_tokens,
                    output_tokens,
                    isoformat_utc(now),
                ),
            )
            return reservation_id

        return self._run_immediate_transaction(reserve, error_type=LlmReservationError)

    def release_llm_reservation(self, reservation_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM llm_reservations WHERE id=?", (reservation_id,))

    def fts_belief_ids(self, episode_id: str, query: str, *, limit: int = 200) -> tuple[str, ...]:
        tokens = re.findall(r"[\w.-]+", query, re.UNICODE)[:20]
        if not tokens:
            return ()
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT belief_id FROM beliefs_fts WHERE beliefs_fts MATCH ? AND episode_id=? "
                    "ORDER BY bm25(beliefs_fts),belief_id LIMIT ?",
                    (expression, episode_id, max(1, min(limit, 1_000))),
                ).fetchall()
            return tuple(str(row[0]) for row in rows)
        except sqlite3.OperationalError:
            return ()

    def descendants(self, episode_id: str, belief_id: str) -> tuple[str, ...]:
        """Return transitive justification descendants in deterministic order."""

        with self.connect() as connection:
            rows = connection.execute(
                "WITH RECURSIVE descendants(id) AS ("
                " SELECT j.belief_id FROM justification_premises p JOIN justifications j ON j.id=p.justification_id WHERE p.premise_belief_id=? AND j.episode_id=?"
                " UNION SELECT j2.belief_id FROM justification_premises p2 JOIN justifications j2 ON j2.id=p2.justification_id JOIN descendants d ON p2.premise_belief_id=d.id WHERE j2.episode_id=?"
                ") SELECT id FROM descendants ORDER BY id",
                (belief_id, episode_id, episode_id),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def events(self, episode_id: str | None = None) -> list[Event]:
        query = "SELECT * FROM events"
        params: tuple[str, ...] = ()
        if episode_id:
            query += " WHERE episode_id=?"
            params = (episode_id,)
        query += " ORDER BY seq"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            return self._authenticated_events(connection, rows)

    def events_by_ids(self, event_ids: Iterable[str]) -> list[Event]:
        with self.connect() as connection:
            return self._events_by_ids(connection, event_ids)

    def _events_by_ids(
        self, connection: sqlite3.Connection, event_ids: Iterable[object]
    ) -> list[Event]:
        ids = [str(event_id) for event_id in event_ids]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT * FROM events WHERE id IN ({placeholders}) ORDER BY seq", ids
        ).fetchall()
        return self._authenticated_events(connection, rows)

    def verify_hash_chain(self) -> tuple[bool, str]:
        expected_heads: dict[str, tuple[int, str]] = {}
        for event in self.events():
            previous = expected_heads.get(event.episode_id, (0, ZERO_HASH))[1]
            if event.previous_hash != previous:
                raise HashChainError(
                    f"event {event.id} previous hash mismatch for episode {event.episode_id}"
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
            calculated = compute_event_hash(previous, body)
            if calculated != event.event_hash:
                raise HashChainError(f"event {event.id} hash mismatch")
            expected_auth = compute_event_auth(self._integrity_key, event.id, event.event_hash)
            if not hmac.compare_digest(event.auth_tag, expected_auth):
                raise HashChainError(f"event {event.id} authentication mismatch")
            expected_heads[event.episode_id] = (event.seq, event.event_hash)

        with self.connect() as connection:
            actual = {
                str(row["episode_id"]): (int(row["seq"]), str(row["event_hash"]))
                for row in connection.execute("SELECT * FROM event_heads")
            }
        if expected_heads != actual:
            raise HashChainError("event head projection does not match event history")
        digest = canonical_json(expected_heads)
        return True, digest

    def _authenticated_events(
        self, connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
    ) -> list[Event]:
        events = [_event_from_row(row) for row in rows]
        if not events:
            return []
        ids = [event.id for event in events]
        auth_rows = connection.execute(
            f"SELECT event_id,event_hash,auth_tag FROM event_auth WHERE event_id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        auth = {
            str(row["event_id"]): (str(row["event_hash"]), str(row["auth_tag"]))
            for row in auth_rows
        }
        hydrated: list[Event] = []
        for event in events:
            stored = auth.get(event.id)
            if stored is None or stored[0] != event.event_hash:
                raise HashChainError(f"event {event.id} authentication record is missing or stale")
            expected_auth = compute_event_auth(self._integrity_key, event.id, event.event_hash)
            if not hmac.compare_digest(stored[1], expected_auth):
                raise HashChainError(f"event {event.id} authentication mismatch")
            hydrated.append(replace(event, auth_tag=stored[1]))
        return hydrated

    def projection_hash(self) -> str:
        with self.connect() as connection:
            return _projection_hash(connection)

    def replay(self) -> ReplayResult:
        self.verify_hash_chain()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            before = _projection_hash(connection)
            with suppress(sqlite3.OperationalError):
                connection.execute("DELETE FROM beliefs_fts")
            connection.execute("DELETE FROM llm_reservations")
            for table in PROJECTION_TABLES:
                connection.execute(f"DELETE FROM {table}")
            rows = connection.execute("SELECT * FROM events ORDER BY seq").fetchall()
            idempotency_batches: dict[tuple[str, str], list[Event]] = {}
            for row in rows:
                event = _event_from_row(row)
                apply_event(connection, event)
                key = event.correlation.get("idempotency_key")
                if key:
                    idempotency_batches.setdefault((event.episode_id, key), []).append(event)
            _restore_idempotency(connection, idempotency_batches)
            after = _projection_hash(connection)
            if before != after:
                connection.rollback()
                raise StoreError(f"projection replay mismatch: before={before} after={after}")
            connection.commit()
            return ReplayResult(len(rows), before, after)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def checkpoint(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def purge_episode(self, episode_id: str, *, confirmation: str) -> PurgeResult:
        """Offline-compaction purge of one episode after exact confirmation.

        This rewrites the database instead of deleting append-only rows in place.
        Hermes must be stopped so no other process can retain an old connection.
        """

        if confirmation != episode_id:
            raise ValueError("purge confirmation must exactly match the episode id")
        if self.get_episode(episode_id) is None:
            raise ValueError("episode does not exist")
        self.verify_hash_chain()
        all_events = self.events()
        preserved = [event for event in all_events if event.episode_id != episode_id]
        removed = len(all_events) - len(preserved)
        with self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.database.name}.purge.", dir=self.database.parent
        )
        os.close(fd)
        temporary = Path(temporary_name)
        temporary.unlink()
        replacement = LedgerStore(
            temporary,
            busy_timeout_ms=self.busy_timeout_ms,
            integrity_key_path=self.integrity_key_path,
        )
        destination = replacement.connect()
        try:
            destination.execute("BEGIN IMMEDIATE")
            for event in preserved:
                _insert_event(destination, event)
                apply_event(destination, event)
            _restore_idempotency(
                destination,
                _idempotency_batches(preserved),
            )
            destination.commit()
        except Exception:
            destination.rollback()
            raise
        finally:
            destination.close()
        replacement.verify_hash_chain()
        projection_hash = replacement.projection_hash()
        replacement.checkpoint()
        try:
            os.replace(temporary, self.database)
            for suffix in ("-wal", "-shm"):
                Path(f"{self.database}{suffix}").unlink(missing_ok=True)
            self.database.chmod(0o600)
            self.verify_hash_chain()
            return PurgeResult(episode_id, removed, len(preserved), projection_hash)
        finally:
            temporary.unlink(missing_ok=True)
            Path(f"{temporary}-wal").unlink(missing_ok=True)
            Path(f"{temporary}-shm").unlink(missing_ok=True)


def _event_from_row(row: sqlite3.Row) -> Event:
    return Event(
        seq=int(row["seq"]),
        id=str(row["id"]),
        episode_id=str(row["episode_id"]),
        timestamp=parse_datetime(str(row["ts"])),
        kind=str(row["kind"]),
        schema_version=int(row["schema_version"]),
        aggregate_type=str(row["aggregate_type"]),
        aggregate_id=str(row["aggregate_id"]),
        correlation=json.loads(str(row["correlation_json"])),
        causal_event_id=str(row["causal_event_id"]) if row["causal_event_id"] else None,
        payload=json.loads(str(row["payload_json"])),
        previous_hash=str(row["previous_hash"]),
        event_hash=str(row["event_hash"]),
    )


def _insert_event(connection: sqlite3.Connection, event: Event) -> None:
    """Persist an already-authenticated event before updating its projections."""

    connection.execute(
        "INSERT INTO events(seq,id,episode_id,ts,kind,schema_version,aggregate_type,aggregate_id,correlation_json,causal_event_id,payload_json,previous_hash,event_hash) "
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
        (event.id, event.event_hash, event.auth_tag),
    )


def _episode_from_row(row: sqlite3.Row) -> Episode:
    return Episode(
        id=str(row["id"]),
        key=str(row["episode_key"]),
        session_id=str(row["session_id"]),
        task_id=str(row["task_id"]),
        platform=str(row["platform"]),
        model=str(row["model"]),
        default_stakes=Stakes(str(row["default_stakes"])),
        current_turn=int(row["current_turn"]),
        created_at=parse_datetime(str(row["created_at"])),
        updated_at=parse_datetime(str(row["updated_at"])),
        compatibility_mode=CompatibilityMode(str(row["compatibility_mode"])),
        llm_calls_used=int(row["llm_calls_used"]),
        input_tokens_used=int(row["input_tokens_used"]),
        output_tokens_used=int(row["output_tokens_used"]),
        state=str(row["state"]),
    )


def _source_from_row(row: sqlite3.Row) -> Source:
    stats = json.loads(str(row["stats_json"]))
    return Source(
        id=str(row["id"]),
        episode_id=str(row["episode_id"]),
        kind=SourceKind(str(row["kind"])),
        integrity=Integrity(str(row["integrity"])),
        name=str(row["name"]),
        root=str(row["root"]),
        competence={
            str(key): float(value) for key, value in json.loads(str(row["competence_json"])).items()
        },
        stats=SourceStats(
            confirmed=int(stats.get("confirmed", 0)),
            defeated=int(stats.get("defeated", 0)),
            samples=int(stats.get("samples", 0)),
        ),
    )


def _evidence_from_row(row: sqlite3.Row) -> Evidence:
    return Evidence(
        id=str(row["id"]),
        episode_id=str(row["episode_id"]),
        kind=str(row["kind"]),
        source_id=str(row["source_id"]),
        payload=str(row["payload"]) if row["payload"] is not None else None,
        content_hash=str(row["content_hash"]),
        metadata=json.loads(str(row["meta_json"])),
        observed_at=parse_datetime(str(row["observed_at"])),
        redacted=bool(row["redacted"]),
    )


def _belief_from_row(
    row: sqlite3.Row,
    evidence: tuple[EvidenceRef, ...],
    justifications: tuple[Justification, ...],
) -> Belief:
    return Belief(
        id=str(row["id"]),
        episode_id=str(row["episode_id"]),
        content=str(row["content"]),
        normalized_content=str(row["normalized_content"]),
        pramana=Pramana(str(row["pramana"])),
        source_id=str(row["source_id"]),
        evidence=evidence,
        justifications=justifications,
        qualifiers=json.loads(str(row["qualifiers_json"])),
        perishability=Perishability(str(row["perishability"])),
        observed_at=parse_datetime(str(row["observed_at"])),
        stakes=Stakes(str(row["stakes"])),
        status=Status(str(row["status"])),
        admission_status=Status(str(row["admission_status"])),
        domain=str(row["domain"]),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        corroboration=int(row["corroboration"]),
        validity=json.loads(str(row["validity_json"])),
    )


def _hydrate_beliefs(connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[Belief]:
    """Hydrate belief relations in batched queries instead of N+1 lookups."""

    if not rows:
        return []
    belief_ids = [str(row["id"]) for row in rows]
    evidence_by_belief: dict[str, list[EvidenceRef]] = {belief_id: [] for belief_id in belief_ids}
    justification_rows: list[sqlite3.Row] = []
    for ids in _chunks(belief_ids):
        placeholders = ",".join("?" for _ in ids)
        for row in connection.execute(
            f"SELECT belief_id,evidence_id,span_json FROM belief_evidence "
            f"WHERE belief_id IN ({placeholders}) ORDER BY belief_id,evidence_id,span_json",
            ids,
        ).fetchall():
            evidence_by_belief[str(row["belief_id"])].append(
                EvidenceRef(
                    evidence_id=str(row["evidence_id"]),
                    span=tuple(json.loads(str(row["span_json"]))) if row["span_json"] else None,
                )
            )
        justification_rows.extend(
            connection.execute(
                f"SELECT id,belief_id,warrant,audit_json,alternatives_json FROM justifications "
                f"WHERE belief_id IN ({placeholders}) ORDER BY belief_id,id",
                ids,
            ).fetchall()
        )
    premise_by_justification: dict[str, list[str]] = {
        str(row["id"]): [] for row in justification_rows
    }
    for ids in _chunks(list(premise_by_justification)):
        placeholders = ",".join("?" for _ in ids)
        for row in connection.execute(
            f"SELECT justification_id,premise_belief_id FROM justification_premises "
            f"WHERE justification_id IN ({placeholders}) ORDER BY justification_id,ordinal",
            ids,
        ).fetchall():
            premise_by_justification[str(row["justification_id"])].append(
                str(row["premise_belief_id"])
            )
    justifications_by_belief: dict[str, list[Justification]] = {
        belief_id: [] for belief_id in belief_ids
    }
    for row in justification_rows:
        justification = _justification_from_parts(row, premise_by_justification[str(row["id"])])
        justifications_by_belief[justification.belief_id].append(justification)
    return [
        _belief_from_row(
            row,
            tuple(evidence_by_belief[str(row["id"])]),
            tuple(justifications_by_belief[str(row["id"])]),
        )
        for row in rows
    ]


def _hydrate_justifications(
    connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
) -> list[Justification]:
    """Hydrate premises for a justification collection in bounded batches."""

    if not rows:
        return []
    justification_ids = [str(row["id"]) for row in rows]
    premise_by_justification: dict[str, list[str]] = {
        justification_id: [] for justification_id in justification_ids
    }
    for ids in _chunks(justification_ids):
        placeholders = ",".join("?" for _ in ids)
        for premise in connection.execute(
            f"SELECT justification_id,premise_belief_id FROM justification_premises "
            f"WHERE justification_id IN ({placeholders}) ORDER BY justification_id,ordinal",
            ids,
        ).fetchall():
            premise_by_justification[str(premise["justification_id"])].append(
                str(premise["premise_belief_id"])
            )
    return [
        _justification_from_parts(row, premise_by_justification[str(row["id"])]) for row in rows
    ]


def _chunks(values: Sequence[str], size: int = 900) -> Iterable[list[str]]:
    for offset in range(0, len(values), size):
        yield list(values[offset : offset + size])


def _belief_evidence(connection: sqlite3.Connection, belief_id: str) -> tuple[EvidenceRef, ...]:
    rows = connection.execute(
        "SELECT evidence_id,span_json FROM belief_evidence WHERE belief_id=? ORDER BY evidence_id,span_json",
        (belief_id,),
    ).fetchall()
    return tuple(
        EvidenceRef(
            evidence_id=str(row["evidence_id"]),
            span=tuple(json.loads(str(row["span_json"]))) if row["span_json"] else None,
        )
        for row in rows
    )


def _belief_justifications(
    connection: sqlite3.Connection, belief_id: str
) -> tuple[Justification, ...]:
    rows = connection.execute(
        "SELECT id,belief_id,warrant,audit_json,alternatives_json FROM justifications WHERE belief_id=? ORDER BY id",
        (belief_id,),
    ).fetchall()
    return tuple(_justification_from_row(connection, row) for row in rows)


def _justification_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> Justification:
    premise_rows = connection.execute(
        "SELECT premise_belief_id FROM justification_premises WHERE justification_id=? ORDER BY ordinal",
        (str(row["id"]),),
    ).fetchall()
    return _justification_from_parts(row, [str(item[0]) for item in premise_rows])


def _justification_from_parts(row: sqlite3.Row, premises: Sequence[str]) -> Justification:
    audit_data = json.loads(str(row["audit_json"])) if row["audit_json"] else None
    audit = (
        ChainAudit(
            paksadharmata=bool(audit_data["paksadharmata"]),
            sapakse_sattvam=bool(audit_data["sapakse_sattvam"]),
            vipakse_asattvam=bool(audit_data["vipakse_asattvam"]),
            evidence_ids=tuple(audit_data.get("evidence_ids", [])),
            fallacies=tuple(audit_data.get("fallacies", [])),
        )
        if audit_data
        else None
    )
    return Justification(
        id=str(row["id"]),
        belief_id=str(row["belief_id"]),
        premises=tuple(premises),
        warrant=str(row["warrant"]),
        audit=audit,
        alternatives=tuple(json.loads(str(row["alternatives_json"]))),
    )


def _verification_from_row(row: sqlite3.Row) -> VerificationTask:
    return VerificationTask(
        id=str(row["id"]),
        episode_id=str(row["episode_id"]),
        belief_id=str(row["belief_id"]),
        method=VerificationMethod(str(row["method"])),
        k_required=int(row["k_required"]),
        budget=int(row["budget"]),
        result=str(row["result"]) if row["result"] is not None else None,
        state=str(row["state"]),
    )


def _projection_hash(connection: sqlite3.Connection) -> str:
    state: dict[str, list[dict[str, Any]]] = {}
    for table in PROJECTION_TABLES:
        columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]
        rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]
        rows.sort(key=canonical_json)
        state[table] = [{column: row.get(column) for column in columns} for row in rows]
    from .events import content_hash

    return content_hash(canonical_json(state))


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _idempotency_storage_key(episode_id: str, idempotency_key: str) -> str:
    """Scope caller-provided keys without changing the public API."""

    return f"{episode_id}:{idempotency_key}"


def _idempotency_batches(events: Iterable[Event]) -> dict[tuple[str, str], list[Event]]:
    batches: dict[tuple[str, str], list[Event]] = {}
    for event in events:
        key = event.correlation.get("idempotency_key")
        if key:
            batches.setdefault((event.episode_id, key), []).append(event)
    return batches


def _restore_idempotency(
    connection: sqlite3.Connection,
    batches: dict[tuple[str, str], list[Event]],
) -> None:
    for (episode_id, key), events in sorted(batches.items()):
        connection.execute(
            "INSERT INTO idempotency(idempotency_key,episode_id,event_ids_json,created_at) VALUES (?,?,?,?)",
            (
                _idempotency_storage_key(episode_id, key),
                episode_id,
                canonical_json([event.id for event in events]),
                isoformat_utc(events[0].timestamp),
            ),
        )
