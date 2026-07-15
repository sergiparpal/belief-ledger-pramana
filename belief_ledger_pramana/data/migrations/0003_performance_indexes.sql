-- Canonical DDL is applied by belief_ledger_pramana.migrations.SCHEMA_V4.
-- This retained marker documents the forward-only index migration for directory
-- plugin and source-distribution consumers.
CREATE INDEX IF NOT EXISTS beliefs_episode_normalized_idx
  ON beliefs(episode_id, normalized_content, id);
CREATE INDEX IF NOT EXISTS verification_tasks_episode_state_idx
  ON verification_tasks(episode_id, state, id);
CREATE INDEX IF NOT EXISTS justification_premises_belief_idx
  ON justification_premises(premise_belief_id, justification_id);
