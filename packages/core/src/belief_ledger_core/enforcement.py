"""Bound approvals and atomic single-use action authorization."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, TypeVar

from .dependencies import RuntimeDependencies
from .events import canonical_json, content_hash, isoformat_utc
from .migrations import ENFORCEMENT_SCHEMA
from .store import _is_busy

_T = TypeVar("_T")

# Version 2 records that the derived decision indexes have been backfilled. It is written by
# the migration rather than by _SCHEMA: a pre-RC3 database must be seen at version 1 first,
# or the backfill it still needs would be skipped.
_DECISION_INDEX_SCHEMA_VERSION = 2
_DECISION_INDEX_SCHEMA_APPLIED_AT = "2026-08-03T00:00:00.000000Z"


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    schema_version: int
    episode_id: str
    turn_id: str
    namespace: str
    tool_name: str
    arguments_hash: str
    target: str
    policy_id: str
    policy_revision: str
    scope: str

    @property
    def digest(self) -> str:
        return content_hash(canonical_json(asdict(self)))


@dataclass(frozen=True, slots=True)
class ApprovalReceipt:
    schema_version: int
    digest: str
    binding: ApprovalBinding
    issued_at: str
    expires_at: str
    state: str


@dataclass(frozen=True, slots=True)
class ActionBinding:
    schema_version: int
    episode_id: str
    turn_id: str
    namespace: str
    tool_name: str
    arguments_hash: str
    target: str
    policy_id: str
    policy_revision: str
    canonicalization_version: int
    policy_content_digest: str
    config_content_digest: str
    stakes: str
    supporting_belief_ids: tuple[str, ...]
    blocking_conflict_ids: tuple[str, ...]
    approval_receipt_digest: str | None = None

    @property
    def digest(self) -> str:
        return content_hash(canonical_json(asdict(self)))


@dataclass(frozen=True, slots=True)
class ActionDecision:
    schema_version: int
    token: str
    token_digest: str
    binding: ActionBinding
    issued_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    schema_version: int
    consumed: bool
    reason_code: str
    token_digest: str


class EnforcementStore:
    """SQLite authorization state with event/state transitions in one transaction."""

    def __init__(
        self,
        database: Path,
        dependencies: RuntimeDependencies,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        requested = database.expanduser().absolute()
        if requested.is_symlink():
            raise ValueError("authorization database must not be a symbolic link")
        self.database = requested.resolve()
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.dependencies = dependencies
        self.busy_timeout_ms = busy_timeout_ms
        self._journal_configured = False
        self._colocated_projections: frozenset[str] = frozenset()
        connection = self._connect()
        try:
            # `executescript` issues an implicit COMMIT, so the schema lands first and the
            # backfill runs in its own transaction. Without that transaction a crash midway
            # could leave the derived tables partly populated but the version stamp absent.
            connection.executescript(_SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._migrate_decision_indexes(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            # Record which ledger projections this database carries. When authorization state
            # is co-located with the ledger (what `BeliefLedger` composes), permit
            # revalidation reads them directly; a table that was present at open and is
            # missing later means the projection was dropped underneath us, which must fail
            # closed rather than silently skip the check.
            self._colocated_projections = frozenset(
                name
                for name in ("beliefs", "episodes", "conflicts")
                if _table_exists(connection, name)
            )
        finally:
            connection.close()
        with suppress(OSError):
            self.database.chmod(0o600)

    @classmethod
    def _migrate_decision_indexes(cls, connection: sqlite3.Connection) -> None:
        """Run the derived-index backfill once, not on every open.

        The backfill full-scans `action_decisions`. Gating it on the schema version keeps
        that cost on databases that predate the derived tables; `_SCHEMA` still creates
        those tables conditionally, so a fresh database is unaffected either way.
        """

        row = connection.execute(
            "SELECT MAX(version) AS version FROM enforcement_schema_migrations"
        ).fetchone()
        version = int(row["version"]) if row is not None and row["version"] is not None else 0
        if version >= _DECISION_INDEX_SCHEMA_VERSION:
            return
        cls._backfill_decision_indexes(connection)
        connection.execute(
            "INSERT OR IGNORE INTO enforcement_schema_migrations(version,applied_at) VALUES (?,?)",
            (_DECISION_INDEX_SCHEMA_VERSION, _DECISION_INDEX_SCHEMA_APPLIED_AT),
        )

    @staticmethod
    def _backfill_decision_indexes(connection: sqlite3.Connection) -> None:
        """Populate derived lookup tables for databases created by older RCs."""

        rows = connection.execute(
            "SELECT token_digest,binding_json FROM action_decisions"
        ).fetchall()
        for row in rows:
            token_digest = str(row["token_digest"])
            binding = _action_binding(json.loads(str(row["binding_json"])))
            connection.execute(
                "INSERT OR IGNORE INTO action_decision_episodes(token_digest,episode_id) "
                "VALUES (?,?)",
                (token_digest, binding.episode_id),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO action_decision_supports(token_digest,belief_id) "
                "VALUES (?,?)",
                ((token_digest, belief_id) for belief_id in binding.supporting_belief_ids),
            )

    def issue_approval(
        self,
        binding: ApprovalBinding,
        *,
        ttl_seconds: int,
        approved: bool = True,
    ) -> ApprovalReceipt | None:
        if ttl_seconds <= 0:
            raise ValueError("approval ttl_seconds must be positive")
        now = self.dependencies.clock.now()
        expires = now + timedelta(seconds=ttl_seconds)
        receipt_id = self.dependencies.identity.new("approval")
        digest = content_hash(canonical_json({"id": receipt_id, "binding": asdict(binding)}))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not approved:
                revoked = connection.execute(
                    "UPDATE approval_receipts SET state='revoked' "
                    "WHERE binding_digest=? AND state='issued'",
                    (binding.digest,),
                ).rowcount
                self._append_event(
                    connection,
                    "APPROVAL_RECEIPT_DENIED",
                    {
                        "receipt_digest": digest,
                        "binding_digest": binding.digest,
                        "binding": asdict(binding),
                        "revoked_prior_receipts": revoked,
                    },
                )
                connection.commit()
                return None
            connection.execute(
                "INSERT INTO approval_receipts(digest,binding_digest,binding_json,issued_at,expires_at,state) VALUES (?,?,?,?,?,'issued')",
                (
                    digest,
                    binding.digest,
                    canonical_json(asdict(binding)),
                    isoformat_utc(now),
                    isoformat_utc(expires),
                ),
            )
            self._append_event(
                connection,
                "APPROVAL_RECEIPT_ISSUED",
                {
                    "receipt_digest": digest,
                    "binding_digest": binding.digest,
                    "binding": asdict(binding),
                    "issued_at": isoformat_utc(now),
                    "expires_at": isoformat_utc(expires),
                },
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return ApprovalReceipt(
            1, digest, binding, isoformat_utc(now), isoformat_utc(expires), "issued"
        )

    def revoke_approval(
        self, receipt_digest: str, *, reason_code: str = "APPROVAL_REVOKED"
    ) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE approval_receipts SET state='revoked' WHERE digest=? AND state='issued'",
                (receipt_digest,),
            ).rowcount
            if updated:
                self._append_event(
                    connection,
                    "APPROVAL_RECEIPT_REVOKED",
                    {"receipt_digest": receipt_digest, "reason_code": reason_code},
                )
            connection.commit()
            return bool(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def current_approval(self, binding: ApprovalBinding) -> ApprovalReceipt | None:
        """Return a live exact-binding receipt without exposing any secret material."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT digest,binding_json,issued_at,expires_at,state "
                "FROM approval_receipts WHERE binding_digest=? "
                "ORDER BY rowid DESC LIMIT 1",
                (binding.digest,),
            ).fetchone()
        finally:
            connection.close()
        if row is None or str(row["state"]) != "issued":
            return None
        if isoformat_utc(self.dependencies.clock.now()) >= str(row["expires_at"]):
            return None
        return ApprovalReceipt(
            1,
            str(row["digest"]),
            _approval_binding(json.loads(str(row["binding_json"]))),
            str(row["issued_at"]),
            str(row["expires_at"]),
            str(row["state"]),
        )

    def issue_action(self, binding: ActionBinding, *, ttl_seconds: int) -> ActionDecision:
        if ttl_seconds <= 0:
            raise ValueError("action ttl_seconds must be positive")
        raw_token = self.dependencies.token.issue(32)
        token_digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = self.dependencies.clock.now()
        expires = now + timedelta(seconds=ttl_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            approval_reason = self._approval_reason(connection, binding)
            if approval_reason:
                self._append_event(
                    connection,
                    "ACTION_DECISION_REJECTED",
                    {
                        "token_digest": token_digest,
                        "reason_code": approval_reason,
                        "binding": asdict(binding),
                    },
                )
                connection.commit()
                raise ValueError(approval_reason)
            connection.execute(
                "INSERT INTO action_decisions(token_digest,binding_digest,binding_json,issued_at,expires_at,state) VALUES (?,?,?,?,?,'issued')",
                (
                    token_digest,
                    binding.digest,
                    canonical_json(asdict(binding)),
                    isoformat_utc(now),
                    isoformat_utc(expires),
                ),
            )
            connection.executemany(
                "INSERT INTO action_decision_supports(token_digest,belief_id) VALUES (?,?)",
                ((token_digest, belief_id) for belief_id in binding.supporting_belief_ids),
            )
            connection.execute(
                "INSERT INTO action_decision_episodes(token_digest,episode_id) VALUES (?,?)",
                (token_digest, binding.episode_id),
            )
            self._append_event(
                connection,
                "ACTION_DECISION_ISSUED",
                {
                    "token_digest": token_digest,
                    "binding_digest": binding.digest,
                    "binding": asdict(binding),
                    "issued_at": isoformat_utc(now),
                    "expires_at": isoformat_utc(expires),
                    "supporting_belief_ids": list(binding.supporting_belief_ids),
                },
            )
            if binding.approval_receipt_digest:
                receipt = connection.execute(
                    "SELECT binding_json FROM approval_receipts WHERE digest=?",
                    (binding.approval_receipt_digest,),
                ).fetchone()
                if receipt:
                    approval_binding = json.loads(str(receipt["binding_json"]))
                    if approval_binding.get("scope") in {"single_use", "exact_action"}:
                        connection.execute(
                            "UPDATE approval_receipts SET state='consumed' "
                            "WHERE digest=? AND state='issued'",
                            (binding.approval_receipt_digest,),
                        )
                        self._append_event(
                            connection,
                            "APPROVAL_RECEIPT_CONSUMED",
                            {"receipt_digest": binding.approval_receipt_digest},
                        )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return ActionDecision(
            1,
            raw_token,
            token_digest,
            binding,
            isoformat_utc(now),
            isoformat_utc(expires),
        )

    def consume_action(
        self,
        raw_token: str,
        presented: ActionBinding,
        *,
        support_is_active: Callable[[tuple[str, ...]], bool] | None = None,
        conflicts_are_closed: Callable[[tuple[str, ...]], bool] | None = None,
    ) -> ConsumeResult:
        """Consume a permit exactly once, revalidating its binding and its live premises.

        Support and conflict revalidation reads the ledger projections when this store shares
        the ledger database, which is what `BeliefLedger` composes. A store opened on its own
        database has no ledger to read: there the callbacks are the contract, and a caller
        that supplies neither gets binding and single-use guarantees only. A projection that
        was present when this store was opened and is missing now always fails closed.
        """

        token_digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM action_decisions WHERE token_digest=?", (token_digest,)
            ).fetchone()
            if row is None:
                return self._reject(connection, token_digest, "TOKEN_NOT_FOUND")
            state = str(row["state"])
            if state != "issued":
                return self._reject(connection, token_digest, f"TOKEN_{state.upper()}")
            if isoformat_utc(self.dependencies.clock.now()) >= str(row["expires_at"]):
                connection.execute(
                    "UPDATE action_decisions SET state='expired' WHERE token_digest=? AND state='issued'",
                    (token_digest,),
                )
                return self._reject(
                    connection, token_digest, "TOKEN_EXPIRED", event="ACTION_DECISION_EXPIRED"
                )
            stored = _action_binding(json.loads(str(row["binding_json"])))
            mismatch = _binding_mismatch(stored, presented)
            if mismatch:
                return self._reject(connection, token_digest, mismatch)
            approval_reason = self._approval_reason(connection, stored, allow_consumed=True)
            if approval_reason:
                return self._reject(connection, token_digest, approval_reason)
            # `None` from a stored check means this database never carried that projection,
            # so the caller's callback is the only available evidence. An unanswered
            # undeterminable check resolves to False: consumption never proceeds on a
            # predicate nobody verified.
            episode_ok = _resolve(self._stored_episode_is_active(connection, stored), None)
            if not episode_ok:
                connection.execute(
                    "UPDATE action_decisions SET state='revoked' "
                    "WHERE token_digest=? AND state='issued'",
                    (token_digest,),
                )
                return self._reject(
                    connection, token_digest, "EPISODE_FINALIZED", event="ACTION_DECISION_REVOKED"
                )
            support_ok = _resolve(
                self._stored_supports_are_active(connection, stored),
                (
                    (lambda: bool(support_is_active(stored.supporting_belief_ids)))
                    if support_is_active is not None
                    else None
                ),
            )
            conflicts_ok = _resolve(
                self._stored_conflicts_are_closed(connection, stored),
                (
                    (lambda: bool(conflicts_are_closed(stored.blocking_conflict_ids)))
                    if conflicts_are_closed is not None
                    else None
                ),
            )
            if support_ok is False:
                connection.execute(
                    "UPDATE action_decisions SET state='revoked' WHERE token_digest=? AND state='issued'",
                    (token_digest,),
                )
                return self._reject(
                    connection,
                    token_digest,
                    "SUPPORT_RETRACTED",
                    event="ACTION_DECISION_REVOKED",
                )
            if conflicts_ok is False:
                connection.execute(
                    "UPDATE action_decisions SET state='revoked' "
                    "WHERE token_digest=? AND state='issued'",
                    (token_digest,),
                )
                return self._reject(
                    connection,
                    token_digest,
                    "OPEN_CONFLICT",
                    event="ACTION_DECISION_REVOKED",
                )
            updated = connection.execute(
                "UPDATE action_decisions SET state='consumed' WHERE token_digest=? AND state='issued'",
                (token_digest,),
            ).rowcount
            if updated != 1:
                return self._reject(connection, token_digest, "TOKEN_RACE_LOST")
            self._append_event(
                connection,
                "ACTION_DECISION_CONSUMED",
                {"token_digest": token_digest, "binding_digest": stored.digest},
            )
            connection.commit()
            return ConsumeResult(1, True, "CONSUMED", token_digest)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def revoke_for_support(self, belief_id: str) -> int:
        connection = self._connect()
        revoked = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT d.token_digest FROM action_decisions d "
                "JOIN action_decision_supports s ON s.token_digest=d.token_digest "
                "WHERE d.state='issued' AND s.belief_id=?",
                (belief_id,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE action_decisions SET state='revoked' WHERE token_digest=? AND state='issued'",
                    (str(row["token_digest"]),),
                )
                self._append_event(
                    connection,
                    "ACTION_DECISION_REVOKED",
                    {
                        "token_digest": str(row["token_digest"]),
                        "reason_code": "SUPPORT_RETRACTED",
                        "belief_id": belief_id,
                    },
                )
                revoked += 1
            connection.commit()
            return revoked
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def revoke_for_episode(self, episode_id: str) -> int:
        """Revoke every outstanding permit bound to a finalized episode."""

        def revoke(connection: sqlite3.Connection) -> int:
            rows = connection.execute(
                "SELECT d.token_digest FROM action_decisions d "
                "JOIN action_decision_episodes e ON e.token_digest=d.token_digest "
                "WHERE d.state='issued' AND e.episode_id=?",
                (episode_id,),
            ).fetchall()
            affected = [str(row["token_digest"]) for row in rows]
            for token_digest in affected:
                connection.execute(
                    "UPDATE action_decisions SET state='revoked' "
                    "WHERE token_digest=? AND state='issued'",
                    (token_digest,),
                )
                self._append_event(
                    connection,
                    "ACTION_DECISION_REVOKED",
                    {"token_digest": token_digest, "reason_code": "EPISODE_FINALIZED"},
                )
            return len(affected)

        return self._run_immediate_transaction(revoke)

    def _run_immediate_transaction(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run one transaction under the bounded busy-retry policy `LedgerStore` uses.

        Finalization revokes permits in a second transaction, so ordinary contention here
        would otherwise surface as a caller-visible failure on an already-finalized episode.
        """

        deadline = time.monotonic() + self.busy_timeout_ms / 1_000
        attempt = 0
        while True:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                result = operation(connection)
                connection.commit()
                return result
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if not _is_busy(exc) or time.monotonic() >= deadline:
                    raise
                attempt += 1
                time.sleep(min(0.05, 0.002 * (2 ** min(attempt, 5))) + random.random() * 0.003)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def events(self) -> tuple[dict[str, Any], ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT seq,id,at,kind,payload_schema_version,payload_json,previous_hash,event_hash FROM enforcement_events ORDER BY seq"
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            {
                "seq": int(row["seq"]),
                "id": str(row["id"]),
                "at": str(row["at"]),
                "kind": str(row["kind"]),
                "payload_schema_version": int(row["payload_schema_version"]),
                "payload": json.loads(str(row["payload_json"])),
                "previous_hash": str(row["previous_hash"]),
                "event_hash": str(row["event_hash"]),
            }
            for row in rows
        )

    def action_state(self, token_digest: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT state FROM action_decisions WHERE token_digest=?", (token_digest,)
            ).fetchone()
        finally:
            connection.close()
        return str(row["state"]) if row else None

    def projection_snapshot(self) -> str:
        connection = self._connect()
        try:
            return enforcement_projection_snapshot(connection)
        finally:
            connection.close()

    def rebuild(self) -> bool:
        """Rebuild decision state from append-only events and verify exact equality."""

        connection = self._connect()
        try:
            # Snapshot inside the transaction that rebuilds. Reading `before` on a separate
            # connection first would let a concurrent writer land between the two, and the
            # equality check would then compare against state that never existed.
            connection.execute("BEGIN IMMEDIATE")
            before = enforcement_projection_snapshot(connection)
            # Reuse the module-level rebuild and snapshot helpers rather than a second copy
            # of the same projection logic: `LedgerStore.replay` calls those, and two
            # implementations of one projection is how they drift.
            rebuild_enforcement_projection(connection)
            after = enforcement_projection_snapshot(connection)
            if before != after:
                raise RuntimeError("enforcement projection replay mismatch")
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _approval_reason(
        self,
        connection: sqlite3.Connection,
        binding: ActionBinding,
        *,
        allow_consumed: bool = False,
    ) -> str | None:
        digest = binding.approval_receipt_digest
        if not digest:
            return None
        row = connection.execute(
            "SELECT * FROM approval_receipts WHERE digest=?", (digest,)
        ).fetchone()
        if row is None:
            return "APPROVAL_NOT_FOUND"
        state = str(row["state"])
        if state == "consumed" and allow_consumed:
            state = "issued"
        if state != "issued":
            return f"APPROVAL_{state.upper()}"
        if isoformat_utc(self.dependencies.clock.now()) >= str(row["expires_at"]):
            connection.execute(
                "UPDATE approval_receipts SET state='expired' WHERE digest=? AND state='issued'",
                (digest,),
            )
            self._append_event(connection, "APPROVAL_RECEIPT_EXPIRED", {"receipt_digest": digest})
            return "APPROVAL_EXPIRED"
        approval = _approval_binding(json.loads(str(row["binding_json"])))
        expected = ApprovalBinding(
            1,
            binding.episode_id,
            binding.turn_id,
            binding.namespace,
            binding.tool_name,
            binding.arguments_hash,
            binding.target,
            binding.policy_id,
            binding.policy_revision,
            approval.scope,
        )
        return None if approval == expected else "APPROVAL_BINDING_MISMATCH"

    def _projection_readable(self, connection: sqlite3.Connection, name: str) -> bool | None:
        """Whether `name` can be read, or `None` when this database never carried it.

        `None` means the ledger is not co-located and the caller's revalidation callbacks are
        the contract. `False` means the projection was present when this store was opened and
        has since disappeared, which is a integrity failure and must fail closed.
        """

        if _table_exists(connection, name):
            return True
        return False if name in self._colocated_projections else None

    def _stored_supports_are_active(
        self, connection: sqlite3.Connection, binding: ActionBinding
    ) -> bool | None:
        if not binding.supporting_belief_ids:
            return True
        readable = self._projection_readable(connection, "beliefs")
        if readable is not True:
            return readable
        placeholders = ",".join("?" for _ in binding.supporting_belief_ids)
        count = connection.execute(
            f"SELECT COUNT(*) FROM beliefs WHERE episode_id=? AND status='in' "
            f"AND id IN ({placeholders})",
            (binding.episode_id, *binding.supporting_belief_ids),
        ).fetchone()[0]
        return int(count) == len(binding.supporting_belief_ids)

    def _stored_episode_is_active(
        self, connection: sqlite3.Connection, binding: ActionBinding
    ) -> bool | None:
        readable = self._projection_readable(connection, "episodes")
        if readable is not True:
            return readable
        row = connection.execute(
            "SELECT state FROM episodes WHERE id=?", (binding.episode_id,)
        ).fetchone()
        return row is not None and str(row["state"]) == "active"

    def _stored_conflicts_are_closed(
        self, connection: sqlite3.Connection, binding: ActionBinding
    ) -> bool | None:
        readable = self._projection_readable(connection, "conflicts")
        if readable is not True:
            return readable
        # Deliberately episode-wide, and therefore stricter than the permit's own
        # blocking_conflict_ids: any open conflict anywhere in the episode blocks
        # consumption. A conflict opened after the permit was issued is precisely the case
        # the binding could not have named, so scoping this to the binding would let it
        # through.
        row = connection.execute(
            "SELECT 1 FROM conflicts WHERE episode_id=? AND state='open' LIMIT 1",
            (binding.episode_id,),
        ).fetchone()
        if row is not None:
            return False
        if not binding.blocking_conflict_ids:
            return True
        # Subsumed by the episode-wide check above while that check stands: reaching here
        # means the episode has no open conflict at all. It is kept as a scoped backstop so
        # narrowing the rule above cannot silently drop the named conflicts, and it uses the
        # same state='open' predicate rather than state!='resolved' so the two cannot
        # disagree if a third conflict state is ever introduced.
        placeholders = ",".join("?" for _ in binding.blocking_conflict_ids)
        unresolved = connection.execute(
            f"SELECT COUNT(*) FROM conflicts "
            f"WHERE episode_id=? AND id IN ({placeholders}) AND state='open'",
            (binding.episode_id, *binding.blocking_conflict_ids),
        ).fetchone()[0]
        return int(unresolved) == 0

    def _reject(
        self,
        connection: sqlite3.Connection,
        token_digest: str,
        reason_code: str,
        *,
        event: str = "ACTION_DECISION_REJECTED",
    ) -> ConsumeResult:
        self._append_event(
            connection, event, {"token_digest": token_digest, "reason_code": reason_code}
        )
        connection.commit()
        return ConsumeResult(1, False, reason_code, token_digest)

    def _append_event(
        self, connection: sqlite3.Connection, kind: str, payload: dict[str, Any]
    ) -> None:
        row = connection.execute(
            "SELECT seq,event_hash FROM enforcement_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq = int(row["seq"]) + 1 if row else 1
        previous = str(row["event_hash"]) if row else "0" * 64
        event_id = self.dependencies.identity.new("event")
        at = isoformat_utc(self.dependencies.clock.now())
        normalized_payload = {"payload_schema_version": 1, **payload}
        body = {
            "seq": seq,
            "id": event_id,
            "at": at,
            "kind": kind,
            "payload_schema_version": 1,
            "payload": normalized_payload,
            "previous_hash": previous,
        }
        event_hash = content_hash(previous + "\x00" + canonical_json(body))
        connection.execute(
            "INSERT INTO enforcement_events(seq,id,at,kind,payload_schema_version,payload_json,previous_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
            (
                seq,
                event_id,
                at,
                kind,
                1,
                canonical_json(normalized_payload),
                previous,
                event_hash,
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        connection.execute("PRAGMA synchronous=FULL")
        # `journal_mode` is a persistent database property, not a per-connection one, and
        # setting it takes a write lock. This store opens a connection per operation, so it
        # is applied once at construction instead of on every open.
        if not self._journal_configured:
            connection.execute("PRAGMA journal_mode=WAL")
            self._journal_configured = True
        return connection

    def verify_hash_chain(self) -> tuple[bool, str]:
        """Verify the independent authorization event chain."""

        previous = "0" * 64
        expected_seq = 1
        for event in self.events():
            if event["seq"] != expected_seq or event["previous_hash"] != previous:
                raise RuntimeError("authorization event chain sequence mismatch")
            body = {
                "seq": event["seq"],
                "id": event["id"],
                "at": event["at"],
                "kind": event["kind"],
                "payload_schema_version": event["payload_schema_version"],
                "payload": event["payload"],
                "previous_hash": event["previous_hash"],
            }
            calculated = content_hash(previous + "\x00" + canonical_json(body))
            if calculated != event["event_hash"]:
                raise RuntimeError("authorization event hash mismatch")
            previous = calculated
            expected_seq += 1
        return True, previous

    def projection_hash(self) -> str:
        return content_hash(self.projection_snapshot())


def _approval_binding(value: dict[str, Any]) -> ApprovalBinding:
    return ApprovalBinding(**value)


def _action_binding(value: dict[str, Any]) -> ActionBinding:
    value["supporting_belief_ids"] = tuple(value.get("supporting_belief_ids", ()))
    value["blocking_conflict_ids"] = tuple(value.get("blocking_conflict_ids", ()))
    return ActionBinding(**value)


def _resolve(stored: bool | None, callback: Callable[[], bool] | None) -> bool:
    """Combine a stored-projection verdict with the caller's revalidation callback.

    `False` from the stored check is final: the projection is co-located and readable, and it
    says the premise no longer holds. `None` means this store cannot judge, so the callback
    decides; with no callback there is nothing to check and nothing was promised.
    """

    if stored is False:
        return False
    if callback is not None:
        return callback()
    return True


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _binding_mismatch(expected: ActionBinding, actual: ActionBinding) -> str | None:
    checks = (
        ("episode_id", "EPISODE_MISMATCH"),
        ("turn_id", "TURN_MISMATCH"),
        ("namespace", "TOOL_NAMESPACE_MISMATCH"),
        ("tool_name", "TOOL_NAME_MISMATCH"),
        ("arguments_hash", "ARGUMENTS_MISMATCH"),
        ("target", "TARGET_MISMATCH"),
        ("policy_id", "POLICY_MISMATCH"),
        ("policy_revision", "POLICY_REVISION_MISMATCH"),
        ("canonicalization_version", "CANONICALIZATION_MISMATCH"),
        ("policy_content_digest", "POLICY_CONTENT_DRIFT"),
        ("config_content_digest", "CONFIG_CONTENT_DRIFT"),
        ("stakes", "STAKES_MISMATCH"),
        ("supporting_belief_ids", "SUPPORT_BINDING_MISMATCH"),
        ("blocking_conflict_ids", "CONFLICT_BINDING_MISMATCH"),
        ("approval_receipt_digest", "APPROVAL_RECEIPT_MISMATCH"),
    )
    for field, reason in checks:
        if getattr(expected, field) != getattr(actual, field):
            return reason
    return None


def rebuild_enforcement_projection(connection: sqlite3.Connection) -> None:
    """Rebuild authorization state in an existing transaction from enforcement events."""

    connection.row_factory = sqlite3.Row
    connection.execute("DELETE FROM action_decision_supports")
    connection.execute("DELETE FROM action_decisions")
    connection.execute("DELETE FROM approval_receipts")
    rows = connection.execute(
        "SELECT kind,payload_json FROM enforcement_events ORDER BY seq"
    ).fetchall()
    for row in rows:
        kind = str(row["kind"])
        payload = json.loads(str(row["payload_json"]))
        if kind == "APPROVAL_RECEIPT_ISSUED":
            connection.execute(
                "INSERT INTO approval_receipts"
                "(digest,binding_digest,binding_json,issued_at,expires_at,state) "
                "VALUES (?,?,?,?,?,'issued')",
                (
                    payload["receipt_digest"],
                    payload["binding_digest"],
                    canonical_json(payload["binding"]),
                    payload["issued_at"],
                    payload["expires_at"],
                ),
            )
            continue
        if kind == "APPROVAL_RECEIPT_DENIED":
            connection.execute(
                "UPDATE approval_receipts SET state='revoked' "
                "WHERE binding_digest=? AND state='issued'",
                (payload["binding_digest"],),
            )
            continue
        approval_state = {
            "APPROVAL_RECEIPT_CONSUMED": "consumed",
            "APPROVAL_RECEIPT_EXPIRED": "expired",
            "APPROVAL_RECEIPT_REVOKED": "revoked",
        }.get(kind)
        if approval_state:
            connection.execute(
                "UPDATE approval_receipts SET state=? WHERE digest=? AND state='issued'",
                (approval_state, payload["receipt_digest"]),
            )
            continue
        if kind == "ACTION_DECISION_ISSUED":
            connection.execute(
                "INSERT INTO action_decisions"
                "(token_digest,binding_digest,binding_json,issued_at,expires_at,state) "
                "VALUES (?,?,?,?,?,'issued')",
                (
                    payload["token_digest"],
                    payload["binding_digest"],
                    canonical_json(payload["binding"]),
                    payload["issued_at"],
                    payload["expires_at"],
                ),
            )
            connection.executemany(
                "INSERT INTO action_decision_supports(token_digest,belief_id) VALUES (?,?)",
                (
                    (payload["token_digest"], belief_id)
                    for belief_id in payload["binding"].get("supporting_belief_ids", ())
                ),
            )
            connection.execute(
                "INSERT INTO action_decision_episodes(token_digest,episode_id) VALUES (?,?)",
                (payload["token_digest"], payload["binding"]["episode_id"]),
            )
            continue
        action_state = {
            "ACTION_DECISION_CONSUMED": "consumed",
            "ACTION_DECISION_EXPIRED": "expired",
            "ACTION_DECISION_REVOKED": "revoked",
        }.get(kind)
        if action_state:
            connection.execute(
                "UPDATE action_decisions SET state=? WHERE token_digest=? AND state='issued'",
                (action_state, payload["token_digest"]),
            )


def enforcement_projection_snapshot(connection: sqlite3.Connection) -> str:
    """Return a deterministic snapshot of every derived authorization table."""

    connection.row_factory = sqlite3.Row
    state: dict[str, list[dict[str, Any]]] = {}
    for table in (
        "approval_receipts",
        "action_decisions",
        "action_decision_episodes",
        "action_decision_supports",
    ):
        rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
        rows.sort(key=canonical_json)
        state[table] = rows
    return canonical_json(state)


def compact_enforcement_events(
    rows: tuple[tuple[Any, ...], ...], excluded_episode_id: str
) -> tuple[tuple[Any, ...], ...]:
    """Remove one episode's authorization audit and rebuild the remaining hash chain."""

    approval_episodes: dict[str, str] = {}
    approval_binding_episodes: dict[str, str] = {}
    action_episodes: dict[str, str] = {}
    retained: list[tuple[Any, ...]] = []
    for row in rows:
        kind = str(row[3])
        payload = json.loads(str(row[5]))
        binding = payload.get("binding")
        episode_id = str(binding.get("episode_id", "")) if isinstance(binding, dict) else ""
        if episode_id and kind.startswith("APPROVAL_RECEIPT_"):
            receipt_digest = str(payload.get("receipt_digest", ""))
            binding_digest = str(payload.get("binding_digest", ""))
            if receipt_digest:
                approval_episodes[receipt_digest] = episode_id
            if binding_digest:
                approval_binding_episodes[binding_digest] = episode_id
        elif kind.startswith("APPROVAL_RECEIPT_"):
            episode_id = approval_episodes.get(str(payload.get("receipt_digest", "")), "")
            if not episode_id:
                episode_id = approval_binding_episodes.get(
                    str(payload.get("binding_digest", "")), ""
                )
        if episode_id and kind.startswith("ACTION_DECISION_"):
            token_digest = str(payload.get("token_digest", ""))
            if token_digest:
                action_episodes[token_digest] = episode_id
        elif kind.startswith("ACTION_DECISION_"):
            episode_id = action_episodes.get(str(payload.get("token_digest", "")), "")
        if episode_id != excluded_episode_id:
            retained.append(row)

    compacted: list[tuple[Any, ...]] = []
    previous = "0" * 64
    for seq, row in enumerate(retained, 1):
        event_id = str(row[1])
        at = str(row[2])
        kind = str(row[3])
        payload_schema_version = int(row[4])
        payload_json = str(row[5])
        body = {
            "seq": seq,
            "id": event_id,
            "at": at,
            "kind": kind,
            "payload_schema_version": payload_schema_version,
            "payload": json.loads(payload_json),
            "previous_hash": previous,
        }
        event_hash = content_hash(previous + "\x00" + canonical_json(body))
        compacted.append(
            (
                seq,
                event_id,
                at,
                kind,
                payload_schema_version,
                payload_json,
                previous,
                event_hash,
            )
        )
        previous = event_hash
    return tuple(compacted)


# The authoritative DDL lives in `migrations.ENFORCEMENT_SCHEMA` so a database created here
# and one migrated by `migrations.migrate` cannot drift into different shapes.
_SCHEMA = ENFORCEMENT_SCHEMA
