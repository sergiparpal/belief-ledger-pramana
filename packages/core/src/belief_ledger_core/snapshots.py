"""Snapshots: a discardable derived cache that bounds replay cost.

Read the invariants before changing anything here. They are the whole point, and a future
contributor optimising replay is exactly the person who would break them.

1. **A snapshot is never the source of truth.** The append-only log is. Any snapshot can be deleted
   at any moment with no loss of information, and `prune` exists so that deleting them is routine.
2. **A snapshot carries a derivation fingerprint** over `LATEST_SCHEMA_VERSION`, the package
   version and the modules that determine projection content. If the fingerprint does not match
   current code the snapshot is **discarded, not upgraded** — upgrading one would mean guessing what
   the old code would have produced, and a guess about history is exactly what an event-sourced
   ledger exists to avoid.
3. **Full replay from origin stays the default.** Acceleration is opt-in via `--from-snapshot`.
4. **Every snapshot is verifiable.** `verify-snapshot` rebuilds twice — once fully, once from the
   newest valid snapshot — and compares every projection table row by row.

See `docs/adr/0014-snapshots-as-a-discardable-cache.md`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .events import canonical_json, content_hash, isoformat_utc
from .migrations import LATEST_SCHEMA_VERSION, PROJECTION_MANIFEST_V2, PROJECTION_TABLES

GLOBAL_SCOPE = "global"

# The modules whose content determines what a projection row looks like. A change to any of them
# invalidates every snapshot, which is the conservative direction: a missed invalidation would
# serve rows the current code would not have produced.
_FINGERPRINT_MODULES = ("projections.py", "migrations.py", "enforcement.py", "models.py")


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    scope: str
    chain_height: int
    projection_name: str
    content_hash: str
    fingerprint: str
    created_at: str

    def as_json(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "chain_height": self.chain_height,
            "projection_name": self.projection_name,
            "content_hash": self.content_hash,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class SnapshotSet:
    """Every projection table captured at one height, under one fingerprint."""

    scope: str
    chain_height: int
    fingerprint: str
    created_at: str
    tables: dict[str, list[list[Any]]]

    @property
    def valid_for_current_code(self) -> bool:
        return self.fingerprint == derivation_fingerprint()


@dataclass(frozen=True, slots=True)
class SnapshotVerification:
    scope: str
    chain_height: int | None
    ok: bool
    reason: str
    differing_table: str | None = None
    differing_row: list[Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "chain_height": self.chain_height,
            "ok": self.ok,
            "reason": self.reason,
            "differing_table": self.differing_table,
            "differing_row": self.differing_row,
        }


def derivation_fingerprint(package_version: str = "") -> str:
    """Bind a snapshot to the code that produced it.

    Digests the schema version, the package version and the source of every module whose content
    determines a projection row. Reading the files is deliberate: a version string is bumped by
    hand and a source digest is not.
    """
    directory = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    digest.update(f"schema={LATEST_SCHEMA_VERSION}\n".encode())
    digest.update(f"package={package_version}\n".encode())
    for name in _FINGERPRINT_MODULES:
        path = directory / name
        digest.update(name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"missing")
    return digest.hexdigest()


def projection_tables(connection: sqlite3.Connection) -> dict[str, list[list[Any]]]:
    """Every projection table, as sorted rows over the manifest's declared columns.

    Sorting by canonical JSON is what makes two rebuilds comparable: SQLite row order is not part
    of the projection's meaning, so an unsorted comparison would report ordering as a difference.
    """
    captured: dict[str, list[list[Any]]] = {}
    for table, columns in PROJECTION_MANIFEST_V2:
        rows = connection.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
        captured[table] = sorted(
            ([_plain(value) for value in row] for row in rows),
            key=canonical_json,
        )
    return captured


def create(
    connection: sqlite3.Connection,
    *,
    scope: str,
    chain_height: int,
    created_at: datetime,
    package_version: str = "",
) -> list[SnapshotRow]:
    """Capture the current projections at `chain_height`. Overwrites the row at that height."""
    fingerprint = derivation_fingerprint(package_version)
    stamp = isoformat_utc(created_at)
    rows: list[SnapshotRow] = []
    for name, table_rows in projection_tables(connection).items():
        serialized = canonical_json(table_rows).encode("utf-8")
        digest = content_hash(serialized)
        connection.execute(
            "INSERT INTO snapshots(scope,chain_height,projection_name,content_hash,payload,"
            "fingerprint,created_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(scope,chain_height,projection_name) DO UPDATE SET "
            "content_hash=excluded.content_hash, payload=excluded.payload, "
            "fingerprint=excluded.fingerprint, created_at=excluded.created_at",
            (
                scope,
                chain_height,
                name,
                digest,
                zlib.compress(serialized),
                fingerprint,
                stamp,
            ),
        )
        rows.append(SnapshotRow(scope, chain_height, name, digest, fingerprint, stamp))
    return rows


def listing(connection: sqlite3.Connection, *, scope: str | None = None) -> list[SnapshotRow]:
    query = (
        "SELECT scope,chain_height,projection_name,content_hash,fingerprint,created_at "
        "FROM snapshots"
    )
    parameters: tuple[Any, ...] = ()
    if scope is not None:
        query += " WHERE scope=?"
        parameters = (scope,)
    query += " ORDER BY scope, chain_height DESC, projection_name"
    return [
        SnapshotRow(str(row[0]), int(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5]))
        for row in connection.execute(query, parameters).fetchall()
    ]


def heights(connection: sqlite3.Connection, *, scope: str) -> list[int]:
    return [
        int(row[0])
        for row in connection.execute(
            "SELECT DISTINCT chain_height FROM snapshots WHERE scope=? ORDER BY chain_height DESC",
            (scope,),
        ).fetchall()
    ]


def prune(connection: sqlite3.Connection, *, scope: str, keep: int) -> int:
    """Keep the newest `keep` heights and delete the rest. Deleting all of them is legitimate."""
    if keep < 0:
        raise ValueError("keep must not be negative")
    retained = heights(connection, scope=scope)[:keep]
    if retained:
        placeholders = ",".join("?" for _ in retained)
        cursor = connection.execute(
            f"DELETE FROM snapshots WHERE scope=? AND chain_height NOT IN ({placeholders})",
            (scope, *retained),
        )
    else:
        cursor = connection.execute("DELETE FROM snapshots WHERE scope=?", (scope,))
    return int(cursor.rowcount or 0)


def load_newest_valid(
    connection: sqlite3.Connection,
    *,
    scope: str,
    max_height: int | None = None,
    package_version: str = "",
) -> SnapshotSet | None:
    """The newest snapshot whose fingerprint matches current code, or `None`.

    A stale fingerprint is not an error and not a repair job: the snapshot is skipped, the caller
    falls back to full replay, and correctness is unaffected because the log is authoritative.
    """
    expected = derivation_fingerprint(package_version)
    for height in heights(connection, scope=scope):
        if max_height is not None and height > max_height:
            continue
        rows = connection.execute(
            "SELECT projection_name,payload,content_hash,fingerprint,created_at FROM snapshots "
            "WHERE scope=? AND chain_height=?",
            (scope, height),
        ).fetchall()
        if not rows:
            continue
        fingerprints = {str(row[3]) for row in rows}
        if fingerprints != {expected}:
            continue
        if {str(row[0]) for row in rows} != set(PROJECTION_TABLES):
            continue
        tables: dict[str, list[list[Any]]] = {}
        corrupt = False
        for name, payload, digest, _, _ in rows:
            # Decompression runs before the content hash can be checked, so it needs its own
            # guard: a truncated payload must be discarded like any other unusable snapshot,
            # never raised out of a replay that has a correct fallback available.
            try:
                raw = zlib.decompress(bytes(payload))
                if content_hash(raw) != str(digest):
                    corrupt = True
                    break
                tables[str(name)] = json.loads(raw.decode("utf-8"))
            except (zlib.error, UnicodeDecodeError, json.JSONDecodeError):
                corrupt = True
                break
        if corrupt:
            continue
        return SnapshotSet(scope, height, expected, str(rows[0][4]), tables)
    return None


def restore(connection: sqlite3.Connection, snapshot: SnapshotSet) -> None:
    """Write a snapshot's rows back into the projection tables.

    The caller is responsible for having cleared them and for replaying every event above
    `snapshot.chain_height` afterwards. This function only puts rows back.

    Insertion runs in reverse manifest order. The manifest is ordered children-first so that the
    delete loop in `replay` can remove rows without tripping a foreign key; putting them back
    requires the opposite order, parents first.
    """
    for table, columns in reversed(PROJECTION_MANIFEST_V2):
        rows = snapshot.tables.get(table, [])
        if not rows:
            continue
        placeholders = ",".join("?" for _ in columns)
        connection.executemany(
            f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES ({placeholders})",
            [tuple(row) for row in rows],
        )


def first_difference(
    left: dict[str, list[list[Any]]], right: dict[str, list[list[Any]]]
) -> tuple[str, list[Any] | None] | None:
    """The first differing table and row, so a failure names something rather than nothing."""
    for table, _ in PROJECTION_MANIFEST_V2:
        left_rows = left.get(table, [])
        right_rows = right.get(table, [])
        if left_rows == right_rows:
            continue
        for index in range(max(len(left_rows), len(right_rows))):
            left_row = left_rows[index] if index < len(left_rows) else None
            right_row = right_rows[index] if index < len(right_rows) else None
            if left_row != right_row:
                return table, left_row if left_row is not None else right_row
        return table, None
    return None


def replay_budget_warning(event_count: int, threshold: int) -> str | None:
    """Make the scaling wall visible before it is hit, which is the point of the finding.

    A threshold of zero disables the warning. This reports; it never refuses.
    """
    if threshold <= 0 or event_count < threshold:
        return None
    return (
        f"full replay processed {event_count} events, at or above the configured "
        f"replay.max_events_warn threshold of {threshold}. Replay cost grows with total history; "
        f"consider `db snapshot create` to bound it, and see docs/operations.md."
    )


def _plain(value: Any) -> Any:
    """Normalize SQLite values into JSON-comparable ones."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
