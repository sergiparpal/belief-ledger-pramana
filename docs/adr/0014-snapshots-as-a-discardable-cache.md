# 0014 — Snapshots are a discardable cache and never the source of truth

- **Status:** accepted, 2026-08-10
- **Constrains** [ADR 0002](0002-event-sourced-sqlite.md) and
  [event compatibility](../event-compatibility.md).

## Context

`db replay` rebuilds every projection by reading every event from origin. That is correct, it is
the reason this repository can prove determinism, and it does not scale: cost grows with total
history, without bound, forever.

The obvious fix is to cache a projection and start from it. The obvious fix is also how
event-sourced systems acquire generational drift — a cached state that no longer corresponds to any
replay of the log, trusted because it is faster to trust it than to check it. Once that happens the
log stops being authoritative in practice while continuing to look authoritative in the code.

## Decision

Add snapshots. Bind them with four invariants, and make each one a test rather than a comment.

### Invariant 1 — a snapshot is never the source of truth

The append-only log is. Any snapshot may be deleted at any moment with no loss of information, and
`db snapshot prune --keep 0` exists so that deleting them is a routine operation rather than a
recovery procedure.

**This is the invariant that matters, and it is the one a future contributor optimising replay
would break.** They would break it by making the snapshot the thing that is loaded and the log the
thing that is consulted when the snapshot is missing — which is the same code with the authority
inverted. `test_deleting_every_snapshot_loses_nothing` runs against every frozen v1 fixture and
fails if it ever stops being true.

### Invariant 2 — a stale fingerprint means discard, never upgrade

Each snapshot carries a derivation fingerprint over `LATEST_SCHEMA_VERSION`, the package version,
and a digest of the source of every module that determines projection content — `projections.py`,
`migrations.py`, `enforcement.py`, `models.py`. Source digests rather than a version string,
because a version string is bumped by hand and a digest is not.

If the fingerprint does not match current code, the snapshot is skipped and replay silently falls
back to full. It is not migrated, not repaired, not partially reused. Upgrading a snapshot means
guessing what the old code would have produced, and a guess about history is precisely what an
event-sourced ledger exists to avoid.

A corrupt payload is treated the same way: the content hash is verified on load, decompression is
guarded, and any failure discards the snapshot rather than raising out of a replay that has a
correct fallback available.

### Invariant 3 — full replay from origin remains the default

`db replay` with no flags reads every event. Acceleration is opt-in via `--from-snapshot`.
`ReplayResult.events_replayed` counts events actually read, so the tests assert which path ran by
event-read count rather than by timing — a timing assertion would be a flake and would not
distinguish "used the snapshot" from "was fast today".

### Invariant 4 — every snapshot is verifiable

`db verify-snapshot` rebuilds twice, once fully from origin and once accelerated from the newest
valid snapshot, and compares every projection table row by row over the declared column manifest,
with rows sorted by canonical JSON so that SQLite's row order is not mistaken for a difference. Any
mismatch exits non-zero naming the first differing table and row.

That command is the reason a snapshot may be trusted for acceleration at all.

## Schema and commands

Schema 8 adds `snapshots(scope, chain_height, projection_name, content_hash, payload, fingerprint,
created_at)`. It adds no event kind and no projection table, so `projection_hash_v1` and
`projection_hash_v2` are both unchanged, and rolling back to schema 7 loses nothing but the cache.

```
hermes belief-ledger db snapshot create [--scope global]
hermes belief-ledger db snapshot list [--scope SCOPE]
hermes belief-ledger db snapshot prune [--keep N]
hermes belief-ledger db replay [--from-snapshot]
hermes belief-ledger db verify-snapshot [--scope global]
```

## The replay budget warning

`replay.max_events_warn` defaults to 50 000. A replay at or above it emits a warning through
`db replay`, and `doctor` carries a `replay_budget` check that warns at the same threshold. It
reports; it never refuses, and it never changes doctor's health verdict.

This is the part of the finding that actually addresses the scaling wall. Snapshots bound the cost
of a replay; the warning is what makes the wall visible *before* it is hit, on a ledger whose
operator has never thought about replay cost. The v1 fixtures are 22 events across six files, so no
timing measurement in this repository can surface the problem — which is exactly why the threshold
is a configured number rather than an inferred one.

## Consequences

- Snapshot restore inserts in reverse manifest order. The manifest is ordered children-first so the
  delete loop in `replay` can clear rows without tripping a foreign key; putting them back needs
  the opposite order. This was found by the foreign-key failure on the first run, not by reading.
- Two existing tests changed. One hardcoded the schema numbers in the migration dry-run output. The
  other rolled the schema stamp back by `LATEST_SCHEMA_VERSION` to force the idempotency-rescoping
  migration to re-run — which silently stopped re-running it the moment a version was added above
  it. Both are recorded in [the findings register](../plan-findings.md) as F-19 and F-20; the
  second was a latent defect in the test, not a consequence of this change.
- Snapshot payloads hold projection rows, which include belief content. They inherit the ledger's
  redaction — the rows are copied from projections that were already redacted at ingestion — but a
  snapshot is as sensitive as the database it came from and belongs in the same encrypted backup
  set, not in a more convenient one.
