# Operations

Start with the host-neutral local surface:

```console
uv run --no-sync belief-ledger --state-root .belief-ledger init --format json
uv run --no-sync belief-ledger --state-root .belief-ledger ledger status --format json
uv run --no-sync belief-ledger --state-root .belief-ledger ledger verify-chain --format json
uv run --no-sync belief-ledger --state-root .belief-ledger ledger replay --format json
uv run --no-sync belief-ledger --state-root .belief-ledger episode list --format json
```

The JSONL gateway is local and unauthenticated; do not expose it as a remote multi-tenant service.
Adapter-specific operations, including the retained Hermes commands below, use their adapter-owned
state paths and boundaries.

The neutral state root contains `ledger.sqlite3`, optional SQLite `-wal`/`-shm` files,
`.ledger.integrity.key`, `config.yaml`, and `policies.json`. Back up the database and its active WAL
set consistently, and retain the matching key, configuration, and policy manifest in the same
encrypted backup set. The integrity key is required for verification and replay; the configuration
and policy values preserve the reviewed operating context used alongside the ledger.

Start with `hermes belief-ledger doctor`. A healthy audited-adapter report requires
Hermes/Python capabilities, enablement, `llm_request`, transform precedence, valid config,
schema/hash integrity, private permissions, and registered tools. It separately reports maximum,
requested, and effective profiles; healthy Hermes is `accepted_final`, not strict. Doctor is
offline and performs only a temporary state-directory write probe.

Routine commands:

```bash
hermes belief-ledger db verify-chain
hermes belief-ledger db migrate --dry-run
hermes belief-ledger db replay
hermes belief-ledger episode list
hermes belief-ledger episode export EPISODE --format jsonl
hermes belief-ledger evaluate --suite all --offline
hermes belief-ledger policy validate
hermes belief-ledger policy inventory
```

For the Hermes profile, WAL checkpoints occur after turns; finalization releases process-local
handles without deleting history. Back up the SQLite database, `-wal`, and `-shm` together while
active, or checkpoint and then copy the main file. Retain the matching private
`locks/ledger.integrity.key`, profile configuration, and policy/source-profile extensions in the
same encrypted backup set. Do not regenerate or substitute the key for an existing database.
Forward migrations create a pre-migration database backup. The current schema version is 7.
Schema v6, introduced in rc2, adds
append-only authorization events and rebuildable receipt/decision projections. Schema v7 adds no
table and no event format: it rewrites stored idempotency keys into the episode-scoped form that
replay rebuilds, which is what lets a database written before that scoping be opened again. Follow
[upgrade and rollback](upgrade-and-rollback.md) before opening older state with newer code.

If chain or event-authentication verification fails, stop effectful work, preserve the database and
integrity key, and restore the matching set from a verified backup or export unaffected episodes.
Do not edit event rows or regenerate the key. If FTS5 is absent, deterministic
lexical selection remains available. Busy errors retry with bounded jitter; persistent contention
makes health degraded and HIGH/CRITICAL gates fail closed.

Nothing bounds how many beliefs an open episode accumulates. `context.max_beliefs` (50) is a
rendering budget, not a store limit, and correctness-sensitive reads deliberately take no limit at
all, so episode length is decided entirely by the host that opens and finalizes episodes. Ingestion
cost follows it: around 500 beliefs one ingestion measured about 96 ms, roughly 27 ms of that in
contradiction detection and 7 ms in relabeling. Episodes well below that length are unaffected. A
host that keeps one episode open indefinitely should expect ingestion latency to keep climbing;
finalizing at natural task boundaries is what keeps it flat. `episode list` reports each episode's
state, so it shows which ones are still open. [ADR 0009](adr/0009-incremental-relabeling.md) holds
the measurements and the conditions for changing this.

The retained `purge` operator command is Hermes-specific; the neutral gateway intentionally has no
purge command. Purge is not a projection-only delete because append-only event payloads would
remain. Stop every Hermes process using the profile, make any separately authorized retention
backup, then run `hermes belief-ledger purge --episode EP_ID --confirm EP_ID`. The command verifies
the chain, compacts all other episodes into a private temporary database, replays their projections,
atomically replaces the database, and verifies it again. Exact confirmation is mandatory; the
operation intentionally does not retain an automatic backup containing the purged episode.

For uninstall, disable the plugin and stop Hermes first. Remove a Git/directory installation with
`hermes plugins remove belief-ledger-pramana`, or a package installation with
`python -m pip uninstall belief-ledger-pramana`. The profile-local
`belief-ledger-pramana/` state directory is retained by design. Delete it only under an explicit
retention decision after any required export; uninstall itself never deletes ledger evidence.
