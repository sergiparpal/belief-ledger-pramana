# Changelog

## Unreleased

- Recorded a measured baseline for the obvious-fix plan in `docs/plan-baseline.md`, including the
  experiment showing that defeat semantics are replay-independent: frozen v1 event and projection
  hashes do not move when `compare_priority` changes.
- Documented the backward-compatible `belief_ledger_pramana` import surface in
  `docs/compat-surface.md`, separating the four promised names from the 77 reachable modules.
- Opened the append-only findings register at `docs/plan-findings.md`.
- Added `scripts/check_doc_invariants.py`, which fails when a documented constant diverges from the
  code it is derived from. Six facts are guarded — `LATEST_SCHEMA_VERSION`, the package version, the
  `requires-python` range, the audited Hermes version and commit, and the CI cryptography override —
  across nine files, plus an assertion that every schema version in `1..LATEST_SCHEMA_VERSION` has
  either a migration SQL file or a `SCHEMA_V*` constant. Wired into `scripts/verify_stage.py` and
  the `replay-claims-evaluations` CI job.
- Stated the current schema version in `docs/operations.md` and `docs/architecture.md`, and the
  supported Python range in `README.md`. The new checker found all three missing on its first run;
  they were absent rather than stale, which no value-comparison check would have caught.
- Corrected the specification's claim that a scalar does not govern defeat. `reliability_rank`, the
  learned competence estimate for a source, is the third of the five lexicographic keys, and for
  SHABDA the same scalar also selects the `shabda_apta_*` band at `type_rank`. The belief's own
  `confidence` field remains genuinely unread. No behaviour changed; see
  [ADR 0010](docs/adr/0010-scalar-competence-in-the-priority-order.md) and the new
  `tests/unit/test_priority_order.py`, which pins the tuple order structurally.
- Bound the self-claim privilege to the user's own channel structurally rather than by call-site
  placement. `is_user_self_claim(source, content)` refuses any source whose kind is not `USER`
  before consulting the pattern, and the runtime now calls it instead of `is_about_user_self`.
  `is_about_user_self` is unchanged and still exported. The privilege is a waiver of cross-source
  verification at HIGH stakes, not a competence adjustment; `docs/threat-model.md` now says so, and
  `tests/unit/test_self_claim_scope.py` characterises the pattern's known limitations — no negation
  handling, English and Spanish only, injectable by any user-channel text.
- Made `recency_rank` a priority key for every perishability class rather than only `fast` and
  `live`. Two `slow` or `stable` beliefs differing only in age previously tied on all five keys and
  both went to `PENDING`, which has no active exit; they now resolve to one `IN` and one `OUT`.
  Recency stays fifth, so it can never overturn integrity, type, reliability or specificity, and
  `positive_over_anupalabdhi` still precedes the whole comparison. Frozen v1 replay fixtures are
  unaffected because defeat semantics are replay-independent. See
  [ADR 0011](docs/adr/0011-unconditional-recency-key.md).
- Moved the timezone-awareness guarantee for `Belief.observed_at` to `Belief.__post_init__`, next to
  where `parse_datetime` and `FixedClock` already enforce the same rule. A naive timestamp is now
  refused when the belief is built rather than later inside defeat resolution.
- Made model-component non-determinism detectable. Every call now records an
  `LLM_CALL_ATTRIBUTION` event carrying provider and model labels, a digest of the prompt, a digest
  of the whole request, a digest of the structured result, and the sampling policy applied. It is a
  new record kind rather than a field on `ComponentVerdict` or `LlmUsage`, both of which appear in
  the frozen v1 fixtures, so frozen hashes and both projection hashes are unchanged.
- Added `hermes belief-ledger llm-divergence [--episode EP_ID] [--json]`, which groups recorded
  calls by prompt and input digest and reports every input that produced more than one distinct
  output. Failed calls are excluded: an error is the absence of an answer, not a second one.
- Added `SamplingPolicy` and `verification.sampling_temperature`, defaulting to `0.0` and validated
  to `[0.0, 2.0]`. Both `HostLlmClient` implementations previously passed `temperature=0.0` as a
  hardcoded literal in two separate places; the policy is now expressed once, carried on
  `StructuredModelRequest` as an additive optional field, and recorded on every call. See
  [ADR 0012](docs/adr/0012-llm-call-attribution.md).
- Added external anchoring of the hash chain. `hermes belief-ledger anchor publish` writes the chain
  root at a height to an append-only JSONL sink whose path must resolve outside the ledger
  directory; `anchor verify` recomputes the local root at every anchored height and exits non-zero
  on a mismatch or an anchored height the chain no longer reaches. `db verify-chain
  --against-anchors` fails if either check fails. This makes local modification followed by
  re-chaining detectable — a tamper `db verify-chain` alone cannot see, because the attacker
  restores the chain's internal consistency. It raises the cost of tampering; it does not prevent
  it, and the threat model says so. Off by default via `anchoring.sink_path: ""`. See
  [ADR 0013](docs/adr/0013-external-chain-anchoring.md).
- Added `LedgerStore.chain_state(up_to_height=...)`, which shares its verification with
  `verify_hash_chain` rather than recomputing the root a second way.
- Added schema 8 and a `snapshots` table: a discardable derived cache that bounds replay cost
  without ever becoming the source of truth. Any snapshot can be deleted at any time with no loss,
  a snapshot whose derivation fingerprint no longer matches the installed code is discarded rather
  than upgraded, `db replay` with no flags still reads every event from origin, and
  `db verify-snapshot` rebuilds twice and compares every projection table row by row. Schema 8 adds
  no event kind and no projection table, so both projection hashes are unchanged. See
  [ADR 0014](docs/adr/0014-snapshots-as-a-discardable-cache.md).
- Added `hermes belief-ledger db snapshot create|list|prune`, `db replay --from-snapshot` and
  `db verify-snapshot`.
- Added `replay.max_events_warn` (default 50 000). A full replay at or above it reports a warning
  through `db replay`, which makes the scaling wall visible before it is hit. It never refuses.
- Fixed a migration test that had silently stopped exercising its migration: it rolled the schema
  stamp back by `LATEST_SCHEMA_VERSION`, so adding a version above the one under test made the
  rollback skip it entirely. It now names the migration it exercises.
- Pinned the backward-compatible import surface. `tests/fixtures/compat_surface.json` records every
  module reachable from `belief_ledger_pramana` and every name it exports, and
  `tests/unit/test_compat_surface.py` fails if a module or name disappears. Nothing asserted this
  before, despite the package being a 1.x compatibility contract.
- Gave packaged policy data one home. `defaults.yaml`, `action-policies.yaml` and
  `source-profiles.yaml` no longer ship a second byte-identical copy in the adapter; it loads
  core's, which it already depends on. The parity test now asserts there is exactly one copy.
- Split `belief_ledger_pramana/runtime.py` (3,233 lines) into a package by pure moves:
  `errors.py`, `helpers.py`, `plugin_runtime.py` and `episode_service.py`, with `__init__.py`
  re-exporting every previously importable name. No behaviour changed. `EpisodeService` remains one
  2,430-line class; splitting it is not a pure move and is recorded as a finding rather than
  attempted.
- Added a source-file size guard at 600 lines with eight reasoned exemptions, each recording a
  ceiling the file may not exceed. A file that falls under the limit must leave the list. See
  [ADR 0015](docs/adr/0015-runtime-module-layout.md).
- Scheduled `LedgerRuntime` for removal in 2.0.0 and pinned its `DeprecationWarning` with a test.
  Its remaining callers were not migrated: `ingest_health` and `authorize_deployment` are
  deployment-gate fixture policy with no `BeliefLedger` equivalent, which a test now asserts.

## v0.2.1 / 1.0.0rc4 - 2026-08-05

GitHub source release correcting the `v0.2.0` product surface. It adds no feature and removes none:
the package layout, public API, CLI, protocol, plugin entry point, and audited Hermes contract are
those of `v0.2.0`. The five synchronized Python distributions advance to `1.0.0rc4` because their
code changed, and remain unpublished to any package registry.

- Advanced all five synchronized workspace distributions to `1.0.0rc4`.
- Made episode lifecycle an in-transaction precondition of permit consumption: a permit bound to a
  finalized episode is refused with `EPISODE_FINALIZED` and revoked, including when finalization's
  revocation never ran.
- Made `finalize_episode` repair a partial finalize on retry, and gave `revoke_for_episode` the
  bounded busy-retry policy the ledger store already used.
- Bounded the gateway JSONL reader: an oversized line is rejected without ever buffering past
  `max_line_bytes`, its remainder is drained to the next newline, and the stream resynchronizes.
- Backed gateway idempotency with the ledger's durable layer, so a replayed `evidence.ingest`
  cannot double-ingest after cache eviction or a process restart.
- Excluded `request_id` from the gateway idempotency fingerprint: a retry under the same key is
  served the cached response instead of `IDEMPOTENCY_KEY_REUSED`. A different payload still fails.
- Scoped the permit conflict check to the binding's episode in both queries and unified them on
  `state='open'`; the check remains deliberately episode-wide and is now documented and pinned.
- Stopped `to_primitive` from serializing underscore-prefixed dataclass fields, which structurally
  keeps `ActionPermit._raw_token` out of every derived representation.
- Version-guarded the authorization decision-index backfill instead of full-scanning on every open.
- Untracked the stray `.kg-ground-audit.jsonl.ckpt` runtime checkpoint and ignored `.kg-*`.
- Added schema 7, which normalizes legacy unscoped idempotency keys to their episode-scoped form. A
  database written before that scoping failed its projection check and could no longer be opened;
  it now migrates forward behind the usual pre-migration backup. No event bytes and no
  `projection_hash_v1` change.
- Made the action gate fail closed rather than raise when an argument cannot be encoded, and guarded
  the unchecked source lookups that raised `KeyError` on other fail-closed paths.
- Fixed `negotiate_profile` reporting a profile the host cannot actually perform, and wired the
  permit revalidation callbacks that were never connected to anything.
- Wrote every timestamp in the trailing-`Z` form. Two writers stored `+00:00`, which sorts before
  digits and silently reversed text ordering for the rows they wrote.
- Validated extension paths at the location they are read from rather than at a second location, and
  stopped `_directories_within` from looping forever on a target outside its root.
- Replaced the Hermes adapter's parallel `ActionGate`, which had already diverged from core on the
  audited `args_hash` encoding, with a re-export; reconciled and pinned the two `HostLlmClient`
  copies, the enforcement DDL and projection applier, the two config validators, and the three
  packaged YAML files.
- Fixed concurrency around runtime health state, verified the hash chain in a streaming pass, and
  made `explain_decision` read in a single pass.
- Bumped cryptography to `50.0.0` in the lock so the frozen resolution matches the override CI
  installs; `49.0.0` is affected by PYSEC-2026-3552. Updated hatchling to `>=1.31.0` and hypothesis
  to `6.165.0`.
- Scoped Dependabot to a single `uv` entry covering the whole workspace. One entry already bumped
  the root and all four member manifests, so the per-package `pip` entries added nothing and the
  `pip` entry for `/` conflicted with `uv` over the same directory.
- Recorded [ADR 0009](docs/adr/0009-incremental-relabeling.md): measurement attributes the
  per-ingestion cost to contradiction detection, not relabeling, so the relabel fixed point stays
  whole-episode and only detection becomes incremental, behind a differential test on emitted
  events. Proposed; no code has changed for it.
- Documented schema 7, the measured ingestion-cost profile and what bounds episode length, the
  raised cryptography override, and added an index of the decision records.
- Raised the clean-install smoke matrix's cryptography override to `>=50.0.0,<51`. It was the one
  path still installing a 49 release against the audited Hermes host after CI moved off it.
- Removed the Hypothesis deadline from the two ledger-backed property tests. Both drive real SQLite
  work per example, so under the gate's coverage instrumentation the 200 ms default measured
  machine load rather than the property and failed the suite while the property itself held.

## v0.2.0 / 1.0.0rc3 - 2026-08-01

GitHub source release for the complete host-neutral RC3 product surface. The five synchronized
Python distributions remain release candidates and are not published to a package registry.

- Advanced all five synchronized workspace distributions to `1.0.0rc3`.
- Added the generic `belief_ledger_core.BeliefLedger` API with normalized evidence, exact approvals,
  opaque single-use permits, output evaluation, explanations, verification, and replay.
- Generalized the strict reference runner to caller-defined descriptors, policies, classifications,
  and handlers; retained the frozen deployment result through example composition and added a CRM
  custom-tool example.
- Added `belief-ledger-gateway`, which owns the neutral `belief-ledger` CLI, local versioned JSONL
  decision service, and optional owned in-process dispatcher.
- Added `belief-ledger-mcp` inspection and complete-inventory proxy modes with an explicit
  direct-upstream bypass warning and a maximum `action_enforce` claim.
- Made the host-neutral quickstart and five-package responsibility model the primary documentation
  path while retaining the Hermes distribution, plugin surface, state paths, and audited contract.
- Extended dependency/claim checks, workspace builds, artifact inspection, smoke matrices, and CI
  definitions for the five-package architecture.
- Hardened permits against policy/config drift, cross-episode references, finalized-episode reuse,
  retracted support, reopened conflicts, replay races, and authorization-audit leakage during purge.
- Made public inputs, configuration, gateway JSONL, MCP inventory/results, immutable records, and
  state-root paths fail closed on malformed, ambiguous, oversized, or symlinked data.
- Migrated the MCP surface to the official SDK 2.x, added injective proxy names and explicit
  upstream result status, and bounded both inventory and output sizes.
- Updated development dependencies and secured the audited Hermes 0.19.0 host combination by
  overriding its vulnerable Pillow and cryptography leaf pins in CI and clean-install smoke tests.
- Added authorization indexes, bounded FTS retrieval, single-pass explanation hydration, exact
  UTF-8 LLM-budget reservation, and focused safety/regression coverage.
- Expanded the host-neutral quickstart, Python API, state-layout, backup, event-integrity, and
  upgrade/rollback documentation; distinguished neutral `.ledger.integrity.key` state from the
  retained Hermes `locks/ledger.integrity.key` layout.

## v0.1.3 / 1.0.0rc2 - 2026-07-30

Repository and supply-chain hardening. No library code changed in this release: the
`belief-ledger-core`, `belief-ledger-pramana`, and `belief-ledger-reference` distributions stay at
`1.0.0rc2` and their package sources are identical to v0.1.2.

- Added `ci-complete`, a single aggregating CI job that fails unless every required job succeeded,
  so the branch ruleset can require one stable check name instead of the individual matrix legs.
  Requiring the legs directly would turn a dropped Python version into a required check that never
  reports again and blocks every merge.
- Pinned every third-party GitHub Action to a full commit SHA with a trailing version comment. A
  tag is mutable and can be repointed by its upstream owner; a commit SHA cannot.
- Declared `permissions: contents: read` on the workflow, added `timeout-minutes` to every job, and
  added a concurrency group that cancels superseded runs on the same ref.
- Bumped `astral-sh/setup-uv` from 7.6.0 to 9.0.0 via Dependabot — the first pinned-SHA bump to
  land under the new policy, confirming that pinning still leaves upgrades visible and reviewable.
- Added `SECURITY.md` with a private advisory reporting path, a 7-day initial response commitment,
  and an explicit scope that names ledger integrity, gate bypass, approval handling, retraction
  correctness, and adapter boundaries as the areas most worth scrutiny.
- Documented the branch ruleset and the CI gating rules in `CLAUDE.md`, including why
  `hermes-main-canary` is deliberately excluded from `ci-complete`'s `needs:` list.
- Replaced untranslated Sanskrit terminology in `README.md` with plain descriptions of what each
  evidence type records, and added a "Why the name Pramana?" section explaining the provenance
  scheme the name refers to.
- Fixed the plugin installation command in `README.md`, which still carried the literal
  `OWNER/REPO` placeholder instead of the actual repository path.

## v0.1.2 / 1.0.0rc2 - 2026-07-24

- Split the project into synchronized `belief-ledger-core`, backward-compatible
  `belief-ledger-pramana`, and strict `belief-ledger-reference` distributions.
- Made enforcement capability-profiled. Hermes is honestly limited to `accepted_final`, while the
  reference adapter demonstrates strict dispatch and buffered delivery.
- Added versioned tool-policy manifests, deterministic profile negotiation, exact approval
  receipts, opaque single-use action decisions, and an append-only enforcement chain.
- Preserved frozen v1 event and projection hashes while adding schema-6 authorization projections
  that rebuild from the enforcement event stream.
- Updated the audited peer-host contract to Hermes Agent `0.19.0` at commit
  `3ef6bbd201263d354fd83ec55b3c306ded2eb72a`.
- Fixed the CI environment setup for the peer-host model: Hermes 0.19.0 is installed after the
  workspace sync and checks run without a later re-sync replacing it.
- Made terminal command strings fail closed; every terminal invocation now requires the
  effectful-action confirmation path.
- Added target-bound direct observations for recognised structured file and environment APIs.
  These can satisfy matching gate prerequisites; free-form tool and terminal output cannot.
- Added recursive structured secret redaction for credentials, headers, cookies, private keys,
  JWTs, common provider tokens, and credential-bearing URIs before evidence or gate data persist.
- Added schema migration 3 with HMAC-SHA-256 event authentication, a private 256-bit local key,
  and replay-time authentication checks. Restricted ledger state, configuration, and extensions
  to private profile-local paths with POSIX and Windows ACL validation.
- Added schema migrations 4 and 5 for hot-path projection indexes, including component-verdict
  lookup, without changing the append-only event model.
- Hardened terminal classification, action-bound confirmation, request-bound context injection,
  and execution-output provenance.
- Made freshness, configuration controls, retraction acknowledgement, and linter citation
  enforcement operational rather than advisory.
- Fixed multi-event idempotency, verification-task races, source-stat deltas, and atomic LLM
  budget reservation; added schema migration 2.
- Added batching for belief hydration, stronger input validation, expanded regression/evaluation
  coverage, package smoke checks, and audited peer-host verification.

## 1.0.0rc1 - 2026-07-11

- Complete standalone Hermes `0.18.2` plugin with directory and entry-point discovery.
- Typed, episode-scoped, hash-chained event ledger with deterministic replay and purge compaction.
- Fixed-point defeat/reinstatement, conflicts, structural retractions, verification, and āpta updates.
- Lazy provenance-aware ingestion, qualified absence, bounded host-LLM components, and auditable verdicts.
- Per-provider-request context injection across four API shapes, final-output linting, and action gating.
- Offline Suites A-D, executable ablations, performance evidence, property/fuzz tests, and release tooling.

This is a local release candidate. No public GitHub or package-registry release has been made.
