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

The report carries three lists, and the difference between them is the verdict. `errors` mean the
adapter is unusable and give `status: unavailable`. `warnings` give `status: degraded` and mean
something a person has to fix — a profile downgraded below what was requested, an unreadable anchor
sink. `notices` never move the verdict: they carry facts that are true of a healthy deployment, such
as a replay approaching its budget, anchoring being switched off, or this host being structurally
unable to offer the strict guarantee. Anything that fires on a correctly configured system belongs
in `notices`, because a warning that is always present is a warning nobody reads.

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
hermes belief-ledger llm-divergence --json
hermes belief-ledger anchor publish
hermes belief-ledger anchor verify
hermes belief-ledger db snapshot create
hermes belief-ledger db verify-snapshot
```

## Bounding replay cost

Replay reads every event from origin, so its cost grows with total history. `db replay` with no
flags always does that, and always will. Snapshots are an opt-in cache on top:

```bash
hermes belief-ledger db snapshot create --scope global
hermes belief-ledger db snapshot list
hermes belief-ledger db verify-snapshot
hermes belief-ledger db replay --from-snapshot
hermes belief-ledger db snapshot prune --keep 3
```

A snapshot is never the source of truth. Delete every one of them at any time and nothing is lost:
the append-only log rebuilds every projection. A snapshot whose derivation fingerprint no longer
matches the installed code is discarded rather than upgraded, and `db replay --from-snapshot`
falls back to a full replay without error — so an upgrade never needs a snapshot migration step.

Run `db verify-snapshot` before relying on acceleration. It rebuilds twice, once fully and once
from the newest valid snapshot, and compares every projection table; a mismatch exits non-zero
naming the first differing table and row.

`replay.max_events_warn` (default 50 000) makes the scaling wall visible before it is hit. Both
`db replay` and `doctor` report it: `doctor` carries a `replay_budget` check with the event count
and the threshold, and adds a **notice** once the count reaches it. Neither refuses, and the notice
does not change doctor's health verdict. Treat the first one as the signal to start snapshotting,
not as an error. Until 2026-08-19 this message was appended to `warnings`, which did move the
verdict — a ledger flipped to `degraded` for nothing worse than having been used.

Snapshot payloads contain projection rows and are as sensitive as the database they came from.
Keep them in the same encrypted backup set.

## Anchoring the chain externally

Set `anchoring.sink_path` to a path outside the ledger directory — the configuration is rejected
otherwise — then publish an anchor whenever you would take a backup:

```bash
hermes belief-ledger anchor publish --scope global
hermes belief-ledger anchor verify --json
hermes belief-ledger db verify-chain --against-anchors
```

`doctor` reports anchoring state in a `checks["anchor"]` block so the control is not silently
unused: an empty `sink_path` is a notice naming the opt-out, a configured sink that cannot be read is
a warning, a sink with no published record is a notice, and a newest anchor that disagrees with the
recomputed root is an **error**. Doctor compares only the newest anchor, because re-chaining changes
the root at every height at or above the edit and each comparison re-streams the log; `db
verify-chain --against-anchors` remains the exhaustive check.

`anchor verify` exits non-zero on any anchored root that disagrees with the recomputed local root,
and on any anchored height the local chain no longer reaches. Both are tamper evidence; the output
names the height and both roots. Back up and access-control the sink separately from the ledger, or
the control adds nothing. Read the [threat model](threat-model.md) for what this does and does not
detect.

## Auditing model-component non-determinism

Every model-component call records an `LLM_CALL_ATTRIBUTION` event carrying the provider and model
labels, a digest of the prompt, a digest of the whole request, a digest of the structured result,
and the sampling policy that was applied. `verification.sampling_temperature` defaults to `0.0` and
is asked of the host on every call.

`temperature: 0.0` reduces non-determinism. It cannot remove it — batching, model routing and
provider-side changes all sit outside this process — so divergence is detected rather than assumed
away:

```bash
hermes belief-ledger llm-divergence --json
hermes belief-ledger llm-divergence --episode EP_ID
```

The command groups recorded calls by prompt and input digest and reports every input that produced
more than one distinct output, with the model label, timestamps and event IDs for each call. An
empty report means no recorded input has yet been answered two different ways; it is not a proof
that the component is deterministic. Failed calls carry no output digest and are excluded, so a
transient provider error is not reported as divergence.

A non-empty report is a fact about history, not an alarm on its own. Read it alongside the model
labels: the same input answered differently by two different model labels is a routing change, and
by one label is provider-side variation.

For the Hermes profile, WAL checkpoints occur after turns; finalization releases process-local
handles without deleting history. Back up the SQLite database, `-wal`, and `-shm` together while
active, or checkpoint and then copy the main file. Retain the matching private
`locks/ledger.integrity.key`, profile configuration, and policy/source-profile extensions in the
same encrypted backup set. Do not regenerate or substitute the key for an existing database.
Forward migrations create a pre-migration database backup. The current schema version is 8.
Schema v6, introduced in rc2, adds
append-only authorization events and rebuildable receipt/decision projections. Schema v7 adds no
table and no event format: it rewrites stored idempotency keys into the episode-scoped form that
replay rebuilds, which is what lets a database written before that scoping be opened again.
Schema v8 adds the `snapshots` table, the discardable cache described above. Follow
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
