"""Append-only SQLite event store and deterministic projections."""

from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import tempfile
import time
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from .events import (
    build_event,
    canonical_json,
    compute_event_hash,
    isoformat_utc,
    parse_datetime,
    to_primitive,
)
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


class StoreError(RuntimeError):
    pass


class HashChainError(StoreError):
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

    def __init__(self, database: Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.database = database.resolve()
        self.busy_timeout_ms = busy_timeout_ms
        self.migration: MigrationResult = migrate(self.database, busy_timeout_ms)

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

    def append_events(
        self,
        episode_id: str,
        drafts: Sequence[EventDraft],
        *,
        correlation: dict[str, str] | None = None,
        idempotency_key: str | None = None,
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

        deadline = time.monotonic() + self.busy_timeout_ms / 1_000
        attempt = 0
        while True:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                if idempotency_key:
                    existing = connection.execute(
                        "SELECT event_ids_json FROM idempotency WHERE idempotency_key=?",
                        (idempotency_key,),
                    ).fetchone()
                    if existing:
                        connection.rollback()
                        ids = json.loads(str(existing[0]))
                        return self.events_by_ids(str(item) for item in ids)

                head = connection.execute(
                    "SELECT seq,event_hash FROM event_heads WHERE episode_id=?",
                    (episode_id,),
                ).fetchone()
                previous_hash = str(head["event_hash"]) if head else ZERO_HASH
                next_seq_row = connection.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM events"
                ).fetchone()
                next_seq = int(next_seq_row[0])
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
                    apply_event(connection, event)
                    events.append(event)
                    previous_hash = event.event_hash
                connection.commit()
                return events
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if not _is_busy(exc) or time.monotonic() >= deadline:
                    raise StoreError(str(exc)) from exc
                attempt += 1
                time.sleep(min(0.05, 0.002 * (2 ** min(attempt, 5))) + random.random() * 0.003)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

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

    def get_belief(self, belief_id: str) -> Belief | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM beliefs WHERE id=?", (belief_id,)).fetchone()
            if not row:
                return None
            evidence = _belief_evidence(connection, belief_id)
            justifications = _belief_justifications(connection, belief_id)
        return _belief_from_row(row, evidence, justifications)

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
            beliefs = [
                _belief_from_row(
                    row,
                    _belief_evidence(connection, str(row["id"])),
                    _belief_justifications(connection, str(row["id"])),
                )
                for row in rows
            ]
        return beliefs

    def find_exact_beliefs(self, episode_id: str, normalized_content: str) -> list[Belief]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM beliefs WHERE episode_id=? AND normalized_content=? ORDER BY id",
                (episode_id, normalized_content),
            ).fetchall()
        return [belief for row in rows if (belief := self.get_belief(str(row[0]))) is not None]

    def list_justifications(self, episode_id: str) -> list[Justification]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,belief_id,warrant,audit_json,alternatives_json FROM justifications WHERE episode_id=? ORDER BY id",
                (episode_id,),
            ).fetchall()
            return [_justification_from_row(connection, row) for row in rows]

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
        return [_event_from_row(row) for row in rows]

    def events_by_ids(self, event_ids: Iterable[str]) -> list[Event]:
        ids = list(event_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM events WHERE id IN ({placeholders}) ORDER BY seq", ids
            ).fetchall()
        return [_event_from_row(row) for row in rows]

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
            for table in PROJECTION_TABLES:
                connection.execute(f"DELETE FROM {table}")
            rows = connection.execute("SELECT * FROM events ORDER BY seq").fetchall()
            for row in rows:
                apply_event(connection, _event_from_row(row))
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
        replacement = LedgerStore(temporary, busy_timeout_ms=self.busy_timeout_ms)
        destination = replacement.connect()
        try:
            destination.execute("BEGIN IMMEDIATE")
            for event in preserved:
                destination.execute(
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
                apply_event(destination, event)
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
        premises=tuple(str(item[0]) for item in premise_rows),
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
