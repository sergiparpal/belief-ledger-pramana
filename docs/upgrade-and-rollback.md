# Upgrade and rollback

Treat each state root as owned by one deployment surface. Neutral core/gateway/MCP/reference roots
normally contain `.ledger.integrity.key`; the backward-compatible Hermes profile contains
`locks/ledger.integrity.key`. Do not merge those layouts or substitute one key for another.

## Before changing code

1. Stop every process using the state root, including JSONL gateway, MCP proxy/inspection,
   reference runner, custom core service, and Hermes processes as applicable.
2. Verify and replay with the currently installed code. For a neutral root managed by a version
   that provides the gateway CLI, run:

   ```console
   belief-ledger --state-root PATH ledger verify-chain --format json
   belief-ledger --state-root PATH ledger replay --format json
   ```

   If the installed version predates the neutral CLI, use that adapter's existing verifier or the
   core chain API; do not open the database with newer code merely to perform the pre-upgrade check.
   For a Hermes profile, run `hermes belief-ledger db verify-chain`,
   `hermes belief-ledger db replay`, and `hermes belief-ledger db migrate --dry-run`. The dry run
   performs no writes and reports the current/target schema and backup requirement.
3. Checkpoint SQLite or consistently copy the database together with its `-wal`/`-shm` files.
   Back up the matching integrity key, configuration, manifests, and operator policy/source-profile
   extensions in the same encrypted set.

## Forward upgrade

Databases behind the current schema move forward on first open. The migration creates
`ledger.sqlite3.pre-vN.<timestamp>.bak` before DDL, where `N` is the first pending migration, so a
database at schema 6 is backed up as `pre-v7`.

The current schema is 7. No migration since rc2 introduces a replacement event format: v1 event
bytes and `projection_hash_v1` remain unchanged throughout. Schema 6 adds enforcement events and
decision projections. Schema 7 adds no table — it rewrites stored `idempotency` rows from the older
unscoped key form into the episode-scoped form, because replay always rebuilds that projection
scoped and a database still holding legacy rows would fail its projection check and refuse to open.
The rewrite drops a legacy row only where the scoped row it maps to already exists.

After upgrading a neutral root, run `ledger status`, `ledger verify-chain`, and `ledger replay` with
the explicit `--state-root`. After upgrading Hermes, run `doctor`, `db verify-chain`, and
`db replay`. In either case, exercise an observe-only canary before effectful work and confirm the
reported effective profile, then validate the active policy again.

## Rollback

Rollback is code rollback plus database restore. Stop all processes, preserve the failed-upgrade
files for investigation, restore the checkpoint or pre-migration database together with its
matching integrity key and configuration, and remove stale `-wal`/`-shm` only while every process
is stopped. Install the prior code and verify the chain before restart. Older code refuses a
database whose recorded schema is newer than the schema it supports instead of opening it, so
restoring the matching `pre-vN` backup is the only supported way back.

Do not point older code at a database after schema-6 enforcement events have been written; it may
not understand the authorization event family. A decision consumed before a crash remains
consumed—never edit its state to retry an external effect. Belief Ledger authorizes at most once;
SQLite and an external side effect are not an exactly-once distributed transaction.
