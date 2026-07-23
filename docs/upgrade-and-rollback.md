# Upgrade and rollback

Stop every Hermes/reference process using the state root. Run `hermes belief-ledger db verify-chain`,
then `hermes belief-ledger db migrate --dry-run`. Checkpoint with SQLite or consistently copy the
database plus `-wal`/`-shm`; retain the private state directory according to policy. Dry run performs
no writes and reports current/target schema plus whether an automatic backup is required.

On first rc2 open, the forward migration moves the database to schema 6 and creates a
`ledger.sqlite3.pre-vN.<timestamp>.bak` before DDL, where `N` is the first pending migration.
Schema 6 adds enforcement events and decision projections; v1 event bytes and
`projection_hash_v1` stay unchanged. After upgrading, run `doctor`, `db verify-chain`, and
`db replay`, then exercise an observe-only canary before effectful work.

Rollback is code rollback plus database restore: stop processes, preserve failed-upgrade files,
restore the checkpoint/pre-migration backup to the configured path, remove stale `-wal`/`-shm` only while
all processes are stopped, install prior code, and verify the chain before restart. Do not point old
code at a database after rc2 enforcement events have been written; older code cannot interpret the
new authorization event family. A decision consumed before a crash remains consumed—never edit its
state to retry an external effect.
