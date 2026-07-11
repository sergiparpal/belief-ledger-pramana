# Operations

Start with `hermes belief-ledger doctor`. A healthy full-conformance report requires audited
Hermes/Python capabilities, enablement, `llm_request`, transform precedence, valid config,
schema/hash integrity, private permissions, and registered tools. Doctor is offline and performs
only a temporary state-directory write probe.

Routine commands:

```bash
hermes belief-ledger db verify-chain
hermes belief-ledger db replay
hermes belief-ledger episode list
hermes belief-ledger episode export EPISODE --format jsonl
hermes belief-ledger evaluate --suite all --offline
```

WAL checkpoints occur after turns; finalization releases process-local handles without deleting
history. Back up the SQLite database, `-wal`, and `-shm` together while active, or checkpoint and
then copy the main file. Forward migrations verify the chain and create a pre-migration backup.

If chain verification fails, stop effectful work, preserve the files, and restore from a verified
backup or export unaffected episodes. Do not edit event rows. If FTS5 is absent, deterministic
lexical selection remains available. Busy errors retry with bounded jitter; persistent contention
makes health degraded and HIGH/CRITICAL gates fail closed.

`purge` is deliberately not a projection-only delete: append-only event payloads would remain.
Stop every Hermes process using the profile, make any separately authorized retention backup,
then run `hermes belief-ledger purge --episode EP_ID --confirm EP_ID`. The command verifies the
chain, compacts all other episodes into a private temporary database, replays their projections,
atomically replaces the database, and verifies it again. Exact confirmation is mandatory; the
operation intentionally does not retain an automatic backup containing the purged episode.

For uninstall, disable the plugin and stop Hermes first. Remove a Git/directory installation with
`hermes plugins remove belief-ledger-pramana`, or a package installation with
`python -m pip uninstall belief-ledger-pramana`. The profile-local
`belief-ledger-pramana/` state directory is retained by design. Delete it only under an explicit
retention decision after any required export; uninstall itself never deletes ledger evidence.
