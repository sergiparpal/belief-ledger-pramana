"""Bound approvals and atomic single-use action authorization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from .dependencies import RuntimeDependencies
from .events import canonical_json, content_hash, isoformat_utc


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
        connection = self._connect()
        try:
            connection.executescript(_SCHEMA)
            self._backfill_decision_indexes(connection)
            connection.commit()
        finally:
            connection.close()
        with suppress(OSError):
            self.database.chmod(0o600)

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
            support_ok = self._stored_supports_are_active(connection, stored)
            conflicts_ok = self._stored_conflicts_are_closed(connection, stored)
            if support_ok and support_is_active is not None:
                support_ok = bool(support_is_active(stored.supporting_belief_ids))
            if conflicts_ok and conflicts_are_closed is not None:
                conflicts_ok = bool(conflicts_are_closed(stored.blocking_conflict_ids))
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

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
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
            connection.commit()
            return len(affected)
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

        before = self.projection_snapshot()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM action_decision_supports")
            connection.execute("DELETE FROM action_decisions")
            connection.execute("DELETE FROM approval_receipts")
            rows = connection.execute(
                "SELECT kind,payload_json FROM enforcement_events ORDER BY seq"
            ).fetchall()
            for row in rows:
                self._apply_projection_event(
                    connection, str(row["kind"]), json.loads(str(row["payload_json"]))
                )
            state: dict[str, list[dict[str, Any]]] = {}
            for table in (
                "approval_receipts",
                "action_decisions",
                "action_decision_episodes",
                "action_decision_supports",
            ):
                projected = [dict(item) for item in connection.execute(f"SELECT * FROM {table}")]
                projected.sort(key=canonical_json)
                state[table] = projected
            after = canonical_json(state)
            if before != after:
                raise RuntimeError("enforcement projection replay mismatch")
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _apply_projection_event(
        self, connection: sqlite3.Connection, kind: str, payload: dict[str, Any]
    ) -> None:
        if kind == "APPROVAL_RECEIPT_ISSUED":
            binding = payload["binding"]
            connection.execute(
                "INSERT INTO approval_receipts"
                "(digest,binding_digest,binding_json,issued_at,expires_at,state) "
                "VALUES (?,?,?,?,?,'issued')",
                (
                    payload["receipt_digest"],
                    payload["binding_digest"],
                    canonical_json(binding),
                    payload["issued_at"],
                    payload["expires_at"],
                ),
            )
        elif kind.startswith("APPROVAL_RECEIPT_"):
            if kind == "APPROVAL_RECEIPT_DENIED":
                connection.execute(
                    "UPDATE approval_receipts SET state='revoked' "
                    "WHERE binding_digest=? AND state='issued'",
                    (payload["binding_digest"],),
                )
                return
            state = {
                "APPROVAL_RECEIPT_CONSUMED": "consumed",
                "APPROVAL_RECEIPT_EXPIRED": "expired",
                "APPROVAL_RECEIPT_REVOKED": "revoked",
            }.get(kind)
            if state:
                connection.execute(
                    "UPDATE approval_receipts SET state=? WHERE digest=? AND state='issued'",
                    (state, payload["receipt_digest"]),
                )
        elif kind == "ACTION_DECISION_ISSUED":
            binding = payload["binding"]
            connection.execute(
                "INSERT INTO action_decisions"
                "(token_digest,binding_digest,binding_json,issued_at,expires_at,state) "
                "VALUES (?,?,?,?,?,'issued')",
                (
                    payload["token_digest"],
                    payload["binding_digest"],
                    canonical_json(binding),
                    payload["issued_at"],
                    payload["expires_at"],
                ),
            )
            connection.executemany(
                "INSERT INTO action_decision_supports(token_digest,belief_id) VALUES (?,?)",
                (
                    (payload["token_digest"], belief_id)
                    for belief_id in binding.get("supporting_belief_ids", ())
                ),
            )
            connection.execute(
                "INSERT INTO action_decision_episodes(token_digest,episode_id) VALUES (?,?)",
                (payload["token_digest"], binding["episode_id"]),
            )
        else:
            state = {
                "ACTION_DECISION_CONSUMED": "consumed",
                "ACTION_DECISION_EXPIRED": "expired",
                "ACTION_DECISION_REVOKED": "revoked",
            }.get(kind)
            if state:
                connection.execute(
                    "UPDATE action_decisions SET state=? WHERE token_digest=? AND state='issued'",
                    (state, payload["token_digest"]),
                )

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

    def _stored_supports_are_active(
        self, connection: sqlite3.Connection, binding: ActionBinding
    ) -> bool:
        if not binding.supporting_belief_ids or not _table_exists(connection, "beliefs"):
            return True
        placeholders = ",".join("?" for _ in binding.supporting_belief_ids)
        count = connection.execute(
            f"SELECT COUNT(*) FROM beliefs WHERE episode_id=? AND status='in' "
            f"AND id IN ({placeholders})",
            (binding.episode_id, *binding.supporting_belief_ids),
        ).fetchone()[0]
        return int(count) == len(binding.supporting_belief_ids)

    def _stored_conflicts_are_closed(
        self, connection: sqlite3.Connection, binding: ActionBinding
    ) -> bool:
        if not _table_exists(connection, "conflicts"):
            return True
        row = connection.execute(
            "SELECT 1 FROM conflicts WHERE episode_id=? AND state='open' LIMIT 1",
            (binding.episode_id,),
        ).fetchone()
        if row is not None:
            return False
        if not binding.blocking_conflict_ids:
            return True
        placeholders = ",".join("?" for _ in binding.blocking_conflict_ids)
        unresolved = connection.execute(
            f"SELECT COUNT(*) FROM conflicts WHERE id IN ({placeholders}) AND state!='resolved'",
            binding.blocking_conflict_ids,
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
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS enforcement_schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
INSERT OR IGNORE INTO enforcement_schema_migrations(version,applied_at)
VALUES (1,'2026-07-22T00:00:00.000000Z');
CREATE TABLE IF NOT EXISTS enforcement_events (
  seq INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_schema_version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS enforcement_events_no_update
BEFORE UPDATE ON enforcement_events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS enforcement_events_no_delete
BEFORE DELETE ON enforcement_events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TABLE IF NOT EXISTS approval_receipts (
  digest TEXT PRIMARY KEY,
  binding_digest TEXT NOT NULL,
  binding_json TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('issued','consumed','expired','revoked'))
);
CREATE TABLE IF NOT EXISTS action_decisions (
  token_digest TEXT PRIMARY KEY,
  binding_digest TEXT NOT NULL,
  binding_json TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('issued','consumed','expired','revoked'))
);
CREATE TABLE IF NOT EXISTS action_decision_supports (
  token_digest TEXT NOT NULL REFERENCES action_decisions(token_digest) ON DELETE CASCADE,
  belief_id TEXT NOT NULL,
  PRIMARY KEY(token_digest,belief_id)
);
CREATE INDEX IF NOT EXISTS action_decision_supports_belief_idx
ON action_decision_supports(belief_id,token_digest);
CREATE TABLE IF NOT EXISTS action_decision_episodes (
  token_digest TEXT PRIMARY KEY REFERENCES action_decisions(token_digest) ON DELETE CASCADE,
  episode_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS action_decision_episodes_episode_idx
ON action_decision_episodes(episode_id,token_digest);
CREATE TRIGGER IF NOT EXISTS approval_receipts_immutable_fields
BEFORE UPDATE OF digest,binding_digest,binding_json,issued_at,expires_at ON approval_receipts
BEGIN SELECT RAISE(ABORT, 'approval binding is immutable'); END;
CREATE TRIGGER IF NOT EXISTS approval_receipts_state_transition
BEFORE UPDATE OF state ON approval_receipts
WHEN OLD.state != 'issued' OR NEW.state NOT IN ('consumed','expired','revoked')
BEGIN SELECT RAISE(ABORT, 'invalid approval state transition'); END;
CREATE TRIGGER IF NOT EXISTS action_decisions_immutable_fields
BEFORE UPDATE OF token_digest,binding_digest,binding_json,issued_at,expires_at ON action_decisions
BEGIN SELECT RAISE(ABORT, 'action binding is immutable'); END;
CREATE TRIGGER IF NOT EXISTS action_decisions_state_transition
BEFORE UPDATE OF state ON action_decisions
WHEN OLD.state != 'issued' OR NEW.state NOT IN ('consumed','expired','revoked')
BEGIN SELECT RAISE(ABORT, 'invalid action state transition'); END;
"""
