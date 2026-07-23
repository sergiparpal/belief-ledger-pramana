"""Forward-only SQLite schema migrations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .events import compute_event_auth


@dataclass(frozen=True, slots=True)
class MigrationResult:
    from_version: int
    to_version: int
    backup: Path | None
    fts5_available: bool


SCHEMA_V1 = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  episode_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  correlation_json TEXT NOT NULL,
  causal_event_id TEXT,
  payload_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS events_episode_seq_idx ON events(episode_id, seq);
CREATE INDEX IF NOT EXISTS events_aggregate_idx ON events(aggregate_type, aggregate_id, seq);

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TABLE IF NOT EXISTS event_heads (
  episode_id TEXT PRIMARY KEY,
  seq INTEGER NOT NULL,
  event_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  episode_key TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  model TEXT NOT NULL,
  default_stakes TEXT NOT NULL,
  current_turn INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  compatibility_mode TEXT NOT NULL,
  llm_calls_used INTEGER NOT NULL DEFAULT 0,
  input_tokens_used INTEGER NOT NULL DEFAULT 0,
  output_tokens_used INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  integrity TEXT NOT NULL,
  name TEXT NOT NULL,
  root TEXT NOT NULL,
  competence_json TEXT NOT NULL,
  stats_json TEXT NOT NULL,
  UNIQUE(episode_id, root, kind)
);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources(id),
  payload TEXT,
  content_hash TEXT NOT NULL,
  meta_json TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  redacted INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS beliefs (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  normalized_content TEXT NOT NULL,
  content_fingerprint TEXT NOT NULL,
  pramana TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources(id),
  qualifiers_json TEXT NOT NULL,
  perishability TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  stakes TEXT NOT NULL,
  status TEXT NOT NULL,
  admission_status TEXT NOT NULL,
  domain TEXT NOT NULL,
  confidence REAL,
  corroboration INTEGER NOT NULL,
  validity_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS beliefs_episode_status_idx ON beliefs(episode_id, status);
CREATE INDEX IF NOT EXISTS beliefs_fingerprint_idx ON beliefs(episode_id, content_fingerprint);

CREATE TABLE IF NOT EXISTS belief_evidence (
  belief_id TEXT NOT NULL REFERENCES beliefs(id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(id),
  span_json TEXT,
  PRIMARY KEY(belief_id, evidence_id, span_json)
);

CREATE TABLE IF NOT EXISTS ingestion_supports (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  belief_id TEXT NOT NULL REFERENCES beliefs(id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(id),
  validity_json TEXT NOT NULL,
  active INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS justifications (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  belief_id TEXT NOT NULL REFERENCES beliefs(id) ON DELETE CASCADE,
  warrant TEXT NOT NULL,
  audit_json TEXT,
  alternatives_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS justification_premises (
  justification_id TEXT NOT NULL REFERENCES justifications(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  premise_belief_id TEXT NOT NULL REFERENCES beliefs(id),
  PRIMARY KEY(justification_id, ordinal)
);

CREATE TABLE IF NOT EXISTS defeats (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  attacker TEXT NOT NULL REFERENCES beliefs(id),
  target TEXT NOT NULL,
  kind TEXT NOT NULL,
  basis TEXT NOT NULL,
  active INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS defeats_target_idx ON defeats(episode_id, target);

CREATE TABLE IF NOT EXISTS verification_tasks (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  belief_id TEXT NOT NULL REFERENCES beliefs(id),
  method TEXT NOT NULL,
  k_required INTEGER NOT NULL,
  budget INTEGER NOT NULL,
  result TEXT,
  state TEXT NOT NULL,
  UNIQUE(episode_id, belief_id, method, state)
);

CREATE TABLE IF NOT EXISTS conflicts (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  left_belief_id TEXT NOT NULL REFERENCES beliefs(id),
  right_belief_id TEXT NOT NULL REFERENCES beliefs(id),
  normalized_scope_json TEXT NOT NULL,
  verification_task_id TEXT NOT NULL REFERENCES verification_tasks(id),
  state TEXT NOT NULL,
  UNIQUE(episode_id, left_belief_id, right_belief_id, state)
);

CREATE TABLE IF NOT EXISTS retraction_notices (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  defeated_belief_id TEXT NOT NULL REFERENCES beliefs(id),
  cause TEXT NOT NULL,
  descendants_json TEXT NOT NULL,
  created_turn INTEGER NOT NULL,
  ttl_turns INTEGER NOT NULL,
  state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rendered_beliefs (
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  belief_id TEXT NOT NULL REFERENCES beliefs(id),
  request_id TEXT NOT NULL,
  turn_number INTEGER NOT NULL,
  rendered_at TEXT NOT NULL,
  PRIMARY KEY(episode_id, belief_id, request_id)
);

CREATE TABLE IF NOT EXISTS source_roots (
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  belief_id TEXT NOT NULL REFERENCES beliefs(id),
  root TEXT NOT NULL,
  transport TEXT,
  PRIMARY KEY(episode_id, belief_id, root)
);

CREATE TABLE IF NOT EXISTS content_fingerprints (
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  belief_id TEXT NOT NULL REFERENCES beliefs(id),
  source_root TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  PRIMARY KEY(episode_id, belief_id)
);

CREATE TABLE IF NOT EXISTS component_verdicts (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  component TEXT NOT NULL,
  purpose TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  outcome TEXT NOT NULL,
  belief_id TEXT REFERENCES beliefs(id),
  detail_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  purpose TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cost REAL,
  latency_ms INTEGER NOT NULL,
  turn_number INTEGER NOT NULL,
  outcome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unpromoted_evidence (
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(id),
  source_profile TEXT NOT NULL,
  state TEXT NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY(episode_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS lint_reports (
  event_id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  response_hash TEXT NOT NULL,
  passed INTEGER NOT NULL,
  report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_decisions (
  event_id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  tool_name TEXT NOT NULL,
  args_hash TEXT NOT NULL,
  outcome TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  detail_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assistant_responses (
  event_id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  turn_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency (
  idempotency_key TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL,
  event_ids_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


SCHEMA_V2 = r"""
CREATE TABLE IF NOT EXISTS llm_reservations (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  turn_number INTEGER NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_reservations_episode_turn_idx
  ON llm_reservations(episode_id, turn_number);
"""


SCHEMA_V3 = r"""
CREATE TABLE IF NOT EXISTS event_auth (
  event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
  event_hash TEXT NOT NULL,
  auth_tag TEXT NOT NULL
);
"""


SCHEMA_V4 = r"""
-- Query paths used on every context compilation, relabel pass, and verification cycle.
CREATE INDEX IF NOT EXISTS beliefs_episode_observed_idx
  ON beliefs(episode_id, observed_at, id);
CREATE INDEX IF NOT EXISTS beliefs_episode_status_observed_idx
  ON beliefs(episode_id, status, observed_at, id);
CREATE INDEX IF NOT EXISTS beliefs_episode_normalized_idx
  ON beliefs(episode_id, normalized_content, id);
CREATE INDEX IF NOT EXISTS sources_episode_id_idx ON sources(episode_id, id);
CREATE INDEX IF NOT EXISTS ingestion_supports_episode_belief_idx
  ON ingestion_supports(episode_id, belief_id, id);
CREATE INDEX IF NOT EXISTS justifications_episode_id_idx ON justifications(episode_id, id);
CREATE INDEX IF NOT EXISTS justifications_belief_id_idx ON justifications(belief_id, id);
CREATE INDEX IF NOT EXISTS justification_premises_belief_idx
  ON justification_premises(premise_belief_id, justification_id);
CREATE INDEX IF NOT EXISTS defeats_episode_id_idx ON defeats(episode_id, id);
CREATE INDEX IF NOT EXISTS verification_tasks_episode_state_idx
  ON verification_tasks(episode_id, state, id);
CREATE INDEX IF NOT EXISTS conflicts_episode_state_idx ON conflicts(episode_id, state, id);
CREATE INDEX IF NOT EXISTS retractions_episode_state_turn_idx
  ON retraction_notices(episode_id, state, created_turn, id);
CREATE INDEX IF NOT EXISTS unpromoted_episode_state_idx
  ON unpromoted_evidence(episode_id, state, evidence_id);
"""


SCHEMA_V5 = r"""
CREATE INDEX IF NOT EXISTS component_verdicts_episode_component_input_idx
  ON component_verdicts(episode_id, component, input_hash);
"""


SCHEMA_V6 = r"""
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


PROJECTION_HASH_ALGORITHM = "sha256-canonical-json"
PROJECTION_HASH_V1_ALGORITHM_VERSION = 1
PROJECTION_HASH_V2_ALGORITHM_VERSION = 2

# This ordered manifest is the immutable 1.0.0rc1 projection contract. Never add,
# remove, reorder, or rename an entry: new projections belong only in v2.
PROJECTION_MANIFEST_V1: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("assistant_responses", ("event_id", "episode_id", "turn_id", "content_hash", "content")),
    (
        "gate_decisions",
        (
            "event_id",
            "episode_id",
            "tool_name",
            "args_hash",
            "outcome",
            "reason_code",
            "detail_json",
        ),
    ),
    ("lint_reports", ("event_id", "episode_id", "response_hash", "passed", "report_json")),
    ("unpromoted_evidence", ("episode_id", "evidence_id", "source_profile", "state", "reason")),
    (
        "llm_usage",
        (
            "id",
            "episode_id",
            "purpose",
            "provider",
            "model",
            "input_tokens",
            "output_tokens",
            "cost",
            "latency_ms",
            "turn_number",
            "outcome",
        ),
    ),
    (
        "component_verdicts",
        (
            "id",
            "episode_id",
            "component",
            "purpose",
            "input_hash",
            "outcome",
            "belief_id",
            "detail_json",
        ),
    ),
    ("content_fingerprints", ("episode_id", "belief_id", "source_root", "fingerprint")),
    ("source_roots", ("episode_id", "belief_id", "root", "transport")),
    ("rendered_beliefs", ("episode_id", "belief_id", "request_id", "turn_number", "rendered_at")),
    (
        "retraction_notices",
        (
            "id",
            "episode_id",
            "defeated_belief_id",
            "cause",
            "descendants_json",
            "created_turn",
            "ttl_turns",
            "state",
        ),
    ),
    (
        "conflicts",
        (
            "id",
            "episode_id",
            "left_belief_id",
            "right_belief_id",
            "normalized_scope_json",
            "verification_task_id",
            "state",
        ),
    ),
    (
        "verification_tasks",
        ("id", "episode_id", "belief_id", "method", "k_required", "budget", "result", "state"),
    ),
    ("defeats", ("id", "episode_id", "attacker", "target", "kind", "basis", "active")),
    ("justification_premises", ("justification_id", "ordinal", "premise_belief_id")),
    (
        "justifications",
        ("id", "episode_id", "belief_id", "warrant", "audit_json", "alternatives_json"),
    ),
    (
        "ingestion_supports",
        ("id", "episode_id", "belief_id", "evidence_id", "validity_json", "active"),
    ),
    ("belief_evidence", ("belief_id", "evidence_id", "span_json")),
    (
        "beliefs",
        (
            "id",
            "episode_id",
            "content",
            "normalized_content",
            "content_fingerprint",
            "pramana",
            "source_id",
            "qualifiers_json",
            "perishability",
            "observed_at",
            "stakes",
            "status",
            "admission_status",
            "domain",
            "confidence",
            "corroboration",
            "validity_json",
        ),
    ),
    (
        "evidence",
        (
            "id",
            "episode_id",
            "kind",
            "source_id",
            "payload",
            "content_hash",
            "meta_json",
            "observed_at",
            "redacted",
        ),
    ),
    (
        "sources",
        ("id", "episode_id", "kind", "integrity", "name", "root", "competence_json", "stats_json"),
    ),
    (
        "episodes",
        (
            "id",
            "episode_key",
            "session_id",
            "task_id",
            "platform",
            "model",
            "default_stakes",
            "current_turn",
            "created_at",
            "updated_at",
            "compatibility_mode",
            "llm_calls_used",
            "input_tokens_used",
            "output_tokens_used",
            "state",
        ),
    ),
    ("idempotency", ("idempotency_key", "episode_id", "event_ids_json", "created_at")),
    ("event_heads", ("episode_id", "seq", "event_hash")),
)

# v2 is a strict superset contract; the frozen v1 definition never changes.
PROJECTION_MANIFEST_V2 = (
    *PROJECTION_MANIFEST_V1,
    (
        "approval_receipts",
        ("digest", "binding_digest", "binding_json", "issued_at", "expires_at", "state"),
    ),
    (
        "action_decisions",
        (
            "token_digest",
            "binding_digest",
            "binding_json",
            "issued_at",
            "expires_at",
            "state",
        ),
    ),
)
PROJECTION_TABLES: tuple[str, ...] = tuple(table for table, _ in PROJECTION_MANIFEST_V2)


def configure_connection(connection: sqlite3.Connection, busy_timeout_ms: int) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    connection.execute("PRAGMA synchronous=FULL")


def migrate(
    database: Path,
    integrity_key: bytes | None = None,
    busy_timeout_ms: int = 5_000,
) -> MigrationResult:
    """Apply all forward migrations and make a backup before upgrading existing data."""

    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existed = database.exists() and database.stat().st_size > 0
    connection = sqlite3.connect(database, isolation_level=None)
    backup: Path | None = None
    try:
        configure_connection(connection, busy_timeout_ms)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        from_version = int(row[0]) if row else 0
        if from_version > 6:
            raise RuntimeError(f"database schema {from_version} is newer than supported schema 6")
        if existed and from_version < 6:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup = database.with_name(f"{database.name}.pre-v{from_version + 1}.{stamp}.bak")
            _online_backup(connection, backup)
        if from_version < 1:
            connection.executescript(SCHEMA_V1)
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (1, now)
            )
        if from_version < 2:
            connection.executescript(SCHEMA_V2)
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (2, now)
            )
        if from_version < 3:
            connection.executescript(SCHEMA_V3)
            if integrity_key is not None:
                for event_id, event_hash in connection.execute("SELECT id,event_hash FROM events"):
                    connection.execute(
                        "INSERT OR REPLACE INTO event_auth(event_id,event_hash,auth_tag) VALUES (?,?,?)",
                        (
                            str(event_id),
                            str(event_hash),
                            compute_event_auth(integrity_key, str(event_id), str(event_hash)),
                        ),
                    )
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (3, now)
            )
        if from_version < 4:
            connection.executescript(SCHEMA_V4)
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (4, now)
            )
        if from_version < 5:
            connection.executescript(SCHEMA_V5)
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (5, now)
            )
        if from_version < 6:
            connection.executescript(SCHEMA_V6)
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (6, now)
            )
        fts5 = _ensure_fts(connection)
    finally:
        connection.close()
    try:
        database.chmod(0o600)
        if backup is not None:
            backup.chmod(0o600)
    except OSError:
        pass
    return MigrationResult(
        from_version=from_version, to_version=6, backup=backup, fts5_available=fts5
    )


def _ensure_fts(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS beliefs_fts USING fts5(belief_id UNINDEXED, episode_id UNINDEXED, status UNINDEXED, content)"
        )
        return True
    except sqlite3.OperationalError:
        return False


def _online_backup(source: sqlite3.Connection, destination_path: Path) -> None:
    """Use SQLite's online backup API so WAL state is included consistently."""

    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
