-- Canonical DDL is applied by belief_ledger_pramana.migrations.SCHEMA_V2.
-- This retained resource makes the operational reservation-table upgrade auditable
-- for directory-plugin and source-distribution consumers.
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
