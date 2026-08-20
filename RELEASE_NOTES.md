# Belief Ledger v0.2.1

This GitHub source release contains all repository changes after `v0.2.0`. It is a correctness
release over the `v0.2.0` product surface: no feature is added and none is removed, and the package
layout, public API, CLI, protocol, plugin entry point, and audited Hermes contract are unchanged.
The five synchronized distributions advance to `1.0.0rc4` because their code changed:
`belief-ledger-core`, `belief-ledger-gateway`, `belief-ledger-mcp`, `belief-ledger-reference`, and
`belief-ledger-pramana`.

## Operators running v0.2.0 should read this

**The finalized-episode permit boundary was enforced only by revocation.** `v0.2.0` claimed permits
were hardened against finalized-episode reuse, but that claim rested on out-of-transaction
bookkeeping: `finalize_episode` revoked permits in a second transaction and `consume_permission`
never re-read episode state. A finalize whose revocation did not run left the episode `finalized`
with live permits, and a retry skipped the revoke entirely because the episode was already
`finalized`, so the condition could not be repaired. Episode lifecycle is now a precondition checked
inside the authorization transaction alongside support and conflict state ([ADR
0008](docs/adr/0008-permit-lifecycle-fails-closed-on-finalized-episodes.md)); the revoke runs
unconditionally so a retry repairs a partial finalize; and `revoke_for_episode` retries on ordinary
SQLite contention.

**Schema 7 can rescue a database that `v0.2.0` could no longer open.** Idempotency keys became
episode-scoped after schema 6, but existing rows kept the unscoped form while only new rows were
written scoped. Because replay always rebuilds that projection scoped, a database holding legacy
rows failed its projection check and refused to open at all. Schema 7 normalizes the stored form
once on first open, behind the usual pre-migration backup. It changes no event bytes and no
`projection_hash_v1`.

**One caller-visible behaviour change.** `request_id` is excluded from the gateway idempotency
fingerprint, so a retry correlated with a fresh `request_id` under the same idempotency key is now
served the cached response instead of `IDEMPOTENCY_KEY_REUSED`. A genuinely different payload under
the same key still fails.

## Highlights

- Backed gateway idempotency with the ledger's durable layer, so a replayed `evidence.ingest`
  cannot double-ingest after cache eviction or a process restart. Bounded the JSONL reader so an
  oversized line is rejected without ever buffering past `max_line_bytes`, its remainder is drained
  to the next newline, and the stream resynchronizes.
- Made the action gate fail closed rather than raise when an argument cannot be encoded, and guarded
  the unchecked source lookups that raised `KeyError` on other fail-closed paths.
- Fixed `negotiate_profile` reporting a profile the host cannot actually perform, and wired the
  permit revalidation callbacks that were never connected to anything.
- Scoped the permit conflict check to the binding's episode in both queries and unified them on
  `state='open'`; stopped `to_primitive` from serializing underscore-prefixed dataclass fields,
  which structurally keeps `ActionPermit._raw_token` out of every derived representation.
- Wrote every timestamp in the trailing-`Z` form. Two writers stored `+00:00`, which sorts before
  digits and silently reversed text ordering for the rows they wrote.
- Replaced the Hermes adapter's parallel `ActionGate`, which had already diverged from core on the
  audited `args_hash` encoding, with a re-export; reconciled and pinned the two `HostLlmClient`
  copies, the enforcement DDL and projection applier, the two config validators, and the three
  packaged YAML files.
- Bumped cryptography to `50.0.0` in the lock and raised the clean-install smoke matrix's override
  to `>=50.0.0,<51`; `49.0.0` is affected by PYSEC-2026-3552 and the smoke matrix was the one path
  still installing it against the audited host.
- Recorded [ADR 0009](docs/adr/0009-incremental-relabeling.md): measurement attributes per-ingestion
  cost to contradiction detection, not relabeling, so the relabel fixed point stays whole-episode
  and only detection becomes incremental. Proposed; no code has changed for it.

The behavioural corrections in this release ship with regression coverage that fails against the
previous code. See [CHANGELOG.md](CHANGELOG.md) for the complete list.

## Compatibility and operations

Frozen v1 event bytes and `projection_hash_v1` remain unchanged. The current database schema is 7;
it adds no table and rewrites stored `idempotency` rows into the episode-scoped form, backed up as
`ledger.sqlite3.pre-v7.<timestamp>.bak` before the migration runs. Rollback remains code rollback
plus database restore from that backup, since older code refuses a database whose recorded schema is
newer than the schema it supports. Neutral core/gateway/MCP/reference state uses
`.ledger.integrity.key`; the retained Hermes profile uses `locks/ledger.integrity.key`. Restore only
the key that belongs to the matching database.

The audited Hermes host still pins stale Pillow and cryptography leaves. The tested combination
keeps Hermes Agent 0.19.0 while overriding those leaves with `Pillow>=12.3,<13` and
`cryptography>=50.0.0,<51`; the expected metadata incompatibility warning is documented, and CI and
the clean-install smoke matrix now both test that secured combination.

## Qualification

The complete local release gate passed on Python 3.13 against the audited Hermes Agent 0.19.0 host:
353 non-live tests at 88.18% combined branch coverage, Ruff formatting and lint over 257 files,
strict mypy across 146 source files, a frozen 80-package lock, dependency/workspace/product-claim
boundaries, the deployment-gate and custom-tool examples, the neutral gateway demo, offline
evaluation Suites A–E, policy and Hermes contract checks, a five-wheel build with content
inspection, Twine metadata validation, `pip-audit` with the two documented Hermes false-positive
exceptions, and clean installs for `core`, `core+gateway`, `core+reference`, `core+gateway+mcp`, and
the secured `hermes` combination — all five reporting `1.0.0rc4`. The ten test warnings are the
intentional `LedgerRuntime` compatibility deprecations and two SQLite `ResourceWarning`s from
garbage-collected test connections.

Two property tests lost their Hypothesis deadline in this release. Both drive real SQLite work per
example, so under the gate's coverage instrumentation the 200 ms default measured machine load
rather than the property, and it failed a gate run while the property itself held. No assertion
changed.

## Distribution

GitHub provides the tag and generated source archives. This release does not publish the five
Python distributions to a package registry, upload built wheels or sdists, sign artifacts, or
contact a live model provider.

## v0.2.0

This GitHub source release contains all repository changes after `v0.1.3`. It completes the
host-neutral RC3 product surface while retaining the backward-compatible Hermes 1.x adapter. The
workspace contains five synchronized `1.0.0rc3` release-candidate distributions:
`belief-ledger-core`, `belief-ledger-gateway`, `belief-ledger-mcp`,
`belief-ledger-reference`, and `belief-ledger-pramana`.

### Highlights

- Added the generic `belief_ledger_core.BeliefLedger` API for lifecycle, normalized evidence,
  exact approvals, opaque single-use permits, output evaluation, query, explanation, chain
  verification, and replay.
- Added the host-neutral `belief-ledger` CLI and bounded JSONL decision protocol. JSONL honestly
  reports `observe`; the optional private in-process dispatcher can prove `action_enforce`.
- Added MCP inspection and complete-inventory proxy modes with an explicit direct-upstream bypass
  warning and a maximum `action_enforce` claim.
- Generalized the deterministic reference runner to caller-defined tools, schemas, policies, and
  handlers while retaining its strict owned-dispatch and buffered-delivery conformance proof.
- Preserved the Hermes distribution, imports, plugin entry point, profile-local state paths, and
  audited Hermes Agent 0.19.0 contract. Hermes remains capped at `accepted_final`, not `strict`.
- Hardened authorization against binding, policy, configuration, lifecycle, support, conflict,
  replay, and audit-leakage failures; strengthened malformed-input, state-path, protocol, and
  immutable-record validation.
- Expanded the host-neutral quickstart, Python API, policy-review, backup, event-integrity, and
  upgrade/rollback documentation.

### Compatibility and operations

Frozen v1 event bytes and `projection_hash_v1` remain unchanged. Schema 6 remains the current
database schema and keeps authorization events in a separate append-only enforcement chain.
Neutral core/gateway/MCP/reference state uses `.ledger.integrity.key`; the retained Hermes profile
uses `locks/ledger.integrity.key`. Restore only the key that belongs to the matching database.

The audited Hermes host still pins stale Pillow and cryptography leaves. The tested combination
keeps Hermes Agent 0.19.0 while overriding those leaves with `Pillow>=12.3,<13` and
`cryptography>=48.0.1,<50`; the expected metadata incompatibility warning is documented and CI
tests that secured combination.

### Qualification

The complete local release gate passed on Python 3.13: 323 non-live tests at 88.05% combined branch
coverage, Ruff formatting/lint, strict mypy across 146 source files, dependency/workspace/product
boundaries, frozen fixtures, offline Suites A–E, both host-neutral examples, policy and Hermes
contract checks, five-wheel build and content inspection, Twine metadata validation, and clean
installs for core, gateway, reference, MCP, and the secured Hermes combination. The eight test
warnings are the intentional `LedgerRuntime` compatibility deprecations.

### Distribution

GitHub provides the tag and generated source archives. This release does not publish the five
Python distributions to a package registry, upload built wheels or sdists, sign artifacts, or
contact a live model provider.

## v0.1.3

This release hardens the repository and its build pipeline. It changes no library code: the
synchronized `belief-ledger-core`, `belief-ledger-pramana`, and `belief-ledger-reference`
distributions remain at `1.0.0rc2`, and their package sources are identical to v0.1.2. Anyone
already running v0.1.2 gains nothing by upgrading the code and loses nothing by staying.

CI now aggregates into `ci-complete`, one job that fails unless every required job succeeded. The
branch ruleset requires that single stable check name rather than the individual matrix legs,
because requiring a leg such as a specific Python version turns a later removal of that version
into a required check that never reports again and blocks every merge permanently. Every
third-party action is pinned to a full commit SHA with a trailing version comment, so an upstream
tag repoint cannot silently change what CI executes. The workflow declares
`permissions: contents: read`, every job carries a `timeout-minutes` bound, and a concurrency group
cancels superseded runs on the same ref. The first bump to land under that policy —
`astral-sh/setup-uv` 7.6.0 to 9.0.0, proposed by Dependabot — confirms the intended property: SHA
pinning freezes what runs without hiding that an upgrade is available.

`SECURITY.md` establishes a private GitHub Security Advisory reporting path with a 7-day initial
response commitment, and states the scope explicitly: ledger integrity, gate bypass, approval
reuse or replay, retraction correctness, and adapter boundaries are the areas most worth scrutiny.
The substantive correctness of an operator-authored policy and the behaviour of the underlying
model are out of scope.

`README.md` no longer relies on untranslated Sanskrit terminology to describe what the ledger
records. Each evidence type is now described by what it actually captures, and a new section
explains that *pramāṇa* names a provenance scheme: the ledger records not just a claim but the
evidential route it arrived by, which is what lets it check admission conditions, trace a decision
to its support, and retract conclusions when that support fails. The documented install command
also no longer ships the literal `OWNER/REPO` placeholder in place of the real repository path.

Two repository settings changed alongside the tag and are not visible in the source tree: the
`main` branch ruleset (added 2026-07-27) requires a pull request and a green `ci-complete` before
merge and blocks deletion and non-fast-forward pushes, and CodeQL default setup (configured
2026-07-29) scans Python and Actions weekly. As with previous tags, GitHub provides the source
archives and no package-registry publication or built-distribution upload is included.

## v0.1.2

This GitHub release contains the synchronized `1.0.0rc2` package candidates. It repositions the
project as evidence-backed policy enforcement for AI agents, extracts a host-neutral core, and adds
a strict standalone reference adapter while preserving the Hermes installation and state paths.
Enforcement guarantees are now capability-profiled. No package-registry publication is part of
this release.

The workspace now ships three synchronized `1.0.0rc2` distributions: host-neutral
`belief-ledger-core`, backward-compatible Hermes adapter `belief-ledger-pramana`, and strict
`belief-ledger-reference`. Core adds v2 tool manifests and schema digests, deterministic capability
profiles, exact replay-resistant approval receipts, digest-only single-use action decisions,
serialized consume/revoke state, and bounded response buffering. Schema 6 adds append-only
authorization events and rebuildable v2 projections while preserving frozen v1 replay hashes.

Hermes is labeled `accepted_final`; it does not claim atomic token consumption or exclusive stream
delivery. The audited host contract is Hermes Agent 0.19.0 at commit
`3ef6bbd201263d354fd83ec55b3c306ded2eb72a`; Hermes is now a peer host rather than
a packaged runtime dependency. The reference adapter owns effectful dispatch and delivery, exposes a versioned JSONL
protocol, and demonstrates strict deployment gating. The final local gate passed 287 tests at
88.28% combined branch coverage, Suites A–E, all dependency/product/contract checks, and fresh
manifest builds plus Twine/inspection/clean-install checks for all three wheels. GitHub Actions run
`29991731616` passed all 15 supported platform, Python, dependency, contract, conformance, and
artifact jobs. GitHub provides the source archives for tag `v0.1.2`; built Python distributions
remain deliberately unpublished.

## 1.0.0rc1 baseline

This local release candidate implements the complete staged Hermes plugin plan against Hermes
Agent 0.18.2 at audited commit `3b2ef789dfcf92f5b7b18c08c59d25948e50857f`.

Highlights include the append-only episode ledger, deterministic defeat/reinstatement,
provenance-aware ingestion, bounded per-request context, auditable structured components,
verification and retractions, final-output grounding policy, and a fail-closed action gate.

The release evidence directory contains offline evaluation and ablation results, performance
measurements, coverage/JUnit reports, dependency inventory, SPDX SBOM, artifact file manifests,
and SHA-256 checksums. Live paid-provider evaluation was intentionally skipped. No artifact was
published or signed, and license metadata remains deferred pending explicit public-release
authorization.
