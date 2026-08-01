# Changelog

## Unreleased

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
