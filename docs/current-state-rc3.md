# Release state: v0.2.0 / 1.0.0rc3

The workspace contains five synchronized local release-candidate distributions: core, gateway,
reference, MCP, and the backward-compatible Hermes adapter. Core is the canonical Python API;
gateway owns the neutral executable; reference is strict conformance evidence; MCP provides
inspection and an action proxy; Hermes retains its 1.x public surfaces.

Frozen v1 event fixtures and released historical documents remain unchanged. Current verification
is defined by `scripts/verify_stage.py all`, including workspace boundaries, product claims, generic
examples, five-wheel inspection, Twine metadata, and clean-install modes. GitHub release `v0.2.0`
publishes this repository state as generated source archives. The five Python distributions remain
unpublished to package registries; no built distribution is uploaded or signed by the release.

## Post-v0.2.0 corrections on `main`

These changes are merged and unreleased. They are listed under `## Unreleased` in `CHANGELOG.md`.

`v0.2.0` claimed permits were hardened against finalized-episode reuse. That claim rested on
out-of-transaction bookkeeping: `finalize_episode` revoked permits in a second transaction, and
`consume_permission` never re-read episode state. A finalize whose revocation did not run left the
episode `finalized` with live permits, and a retry skipped the revoke entirely because the episode
was already `finalized`, so the condition could not be repaired. Episode state is now checked inside
the authorization transaction alongside support and conflict state (ADR 0008), the revoke runs
unconditionally so a retry repairs a partial finalize, and `revoke_for_episode` retries on ordinary
SQLite contention. Operators running `v0.2.0` should treat the finalized-episode permit boundary as
enforced only by revocation.

The gateway JSONL reader now enforces `max_line_bytes` while reading rather than after the whole
line is in memory. See `docs/gateway-protocol.md` for the resynchronization behaviour.

Gateway idempotency no longer rests on an in-memory cache alone. `evidence.ingest` passes its key
down to the ledger's durable idempotency layer, so a replay after LRU eviction or a process restart
no longer ingests twice. `request_id` is excluded from the fingerprint, so a retry correlated with a
fresh `request_id` is served the cached response rather than `IDEMPOTENCY_KEY_REUSED`; a genuinely
different payload under the same key still fails. This is the one caller-visible behaviour change in
this set.

Smaller corrections: the permit conflict check is episode-scoped in both of its queries and uses one
state predicate, with its deliberately episode-wide scope documented in `docs/python-api.md` and
pinned by a test; `to_primitive` cannot emit underscore-prefixed dataclass fields, which closes the
latent `ActionPermit._raw_token` path structurally rather than by convention; the authorization
decision-index backfill is guarded by `enforcement_schema_migrations` version 2 instead of running
on every open; and a test pins that the enforcement schema is identical whether a database is
created through `migrations.py` or `enforcement.py`, which `LedgerStore.purge_episode` depends on.

An exhaustive review of the five-package workspace on 2026-08-05 corrected a further set, each with
regression coverage that fails against the previous code. The one operators must know about is
schema 7. Idempotency keys became episode-scoped after schema 6, but existing rows kept the unscoped
form while only new rows were written scoped; because replay always rebuilds that projection scoped,
a database holding legacy rows failed its projection check and could no longer be opened at all.
Schema 7 normalizes the stored form once on first open, behind the usual pre-migration backup, and
changes no event bytes and no `projection_hash_v1`.

The remainder are contained: the action gate fails closed instead of raising when an argument cannot
be encoded; unguarded source lookups no longer raise `KeyError` on fail-closed paths;
`negotiate_profile` no longer reports a profile the host cannot perform; permit revalidation
callbacks are wired; two writers that stored `+00:00` rather than the trailing-`Z` form no longer
reverse text ordering; extension paths are validated where they are read; and `_directories_within`
terminates for a target outside its root. Implementations that had been duplicated and had begun to
diverge — the adapter's parallel `ActionGate`, two `HostLlmClient` copies, the enforcement DDL and
projection applier, two config validators, three packaged YAML files — are reconciled and pinned.
The lock now resolves cryptography `50.0.0`, matching the override CI installs against the audited
Hermes host.

Per-ingestion cost is now measured rather than assumed, and the measurement is recorded in
[ADR 0009](adr/0009-incremental-relabeling.md). Contradiction detection, not relabeling, is the term
that grows with episode length; relabeling stays whole-episode because reinstatement, defeat cycles,
the iteration ceiling, time-driven staleness, and equal-priority conflicts are properties of the
complete graph. The record is proposed and no code has changed for it, so what ships today is three
whole-episode passes per ingestion, costing roughly 96 ms at around 500 beliefs.
