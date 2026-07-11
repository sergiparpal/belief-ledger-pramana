# Configuration

Precedence is explicit `BELIEF_LEDGER_PRAMANA_CONFIG`, profile-local config, then packaged
defaults. First use atomically initializes the profile-local file. Mid-turn edits do not change
the active immutable snapshot; reload occurs at a safe turn boundary. Changing the database
path requires a process restart.

`mode` is `observe`, `warn`, or `enforce`; enforcing is the default. The sections are:

- `storage`: database path, `hash_only|excerpt|full`, redaction, timeout.
- `context`: 8,000-character hard cap, belief/depth limits, retraction TTL, relevance mode.
- `ingestion`: lazy work and atomicity/dedup bounds.
- `verification`: per-turn/episode call and token budgets plus timeout.
- `lint`: stakes-specific output action and the pending marker.
- `gating`: conservative unknown policy and fail-closed threshold.
- `priority`: integrity/type/reliability/specificity/recency ranks.
- `trust`: complete source × stakes matrix, yogyatā, and smoothed āpta settings.
- `perishability_ttl`: stable/slow/fast/live freshness.

Invalid safety-critical settings make component health visibly degraded. No permissive fallback
is silent. Action/source registries are packaged, versioned YAML; operator extensions must use
exact names or anchored regular expressions.

