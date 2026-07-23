# Configuration

Precedence is explicit `BELIEF_LEDGER_PRAMANA_CONFIG`, profile-local config, then packaged
defaults. First use atomically initializes the profile-local file. Mid-turn edits do not change
the active immutable snapshot; reload occurs at a safe turn boundary. Changing the database
path requires a process restart.

`mode` is `observe`, `warn`, or `enforce`; enforcing is the default. The sections are:

- `enforcement`: requested capability profile and explicit diagnostic-downgrade switch. Hermes
  defaults to `accepted_final`; missing capabilities fail closed in `enforce` unless downgrade is
  explicitly enabled. Requested/effective profiles and reason codes are persisted.
- `storage`: database path, `hash_only|excerpt|full`, redaction, timeout.
- `context`: 8,000-character hard cap, belief/depth limits, retraction TTL, and an explicit
  relevance mode (`fts5` or `none`).
- `ingestion`: lazy work, atomicity/dedup bounds, and the explicit relative-workspace trust opt-in.
- `verification`: per-turn/episode call and token budgets plus timeout; model calls reserve budget
  atomically before dispatch.
- `lint`: stakes-specific output action and the pending marker.
- `gating`: conservative unknown policy, fail-closed threshold, and short-lived action-bound
  confirmation TTL.
- `priority`: integrity/type/reliability/specificity/recency ranks.
- `trust`: complete source × stakes matrix, yogyatā, and smoothed āpta settings.
- `perishability_ttl`: stable/slow/fast/live freshness. Expired IN beliefs become PENDING before
  context rendering, inference, or action-gate evaluation.

Invalid safety-critical settings make component health visibly degraded. No permissive fallback
is silent. Tool manifests normalize v1 YAML into schema v2. Exact rules precede anchored patterns;
ambiguous/conflicting effect classifications fail validation. Canonical schema drift ignores only
specified informational fields and never claims semantic equivalence. Generated scaffolds remain
inactive until explicit review.

Configuration, the ledger database, and operator policy/source-profile extensions must all be
regular files below the profile-local plugin state directory. Relative database and extension
paths resolve there; escaping that directory, symbolic links, files larger than 1 MB, or paths
with group/other POSIX access are rejected. On Windows, the plugin also rejects ACLs granting
access to broad principals such as `Users`, `Authenticated Users`, or `Everyone`.

The state directory also contains `locks/ledger.integrity.key`. This generated 256-bit key is not
a configurable setting: it authenticates the event log separately from SQLite and must remain a
private regular file. Retain its matching value in an encrypted backup with the ledger database;
restoring the database with a new or different key causes event-authentication verification to
fail. See [operations.md](operations.md) for the backup procedure.
