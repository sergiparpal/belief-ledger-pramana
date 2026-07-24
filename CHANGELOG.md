# Changelog

## Unreleased

- Fixed the CI environment setup for the peer-host model: Hermes 0.19.0 is installed after the
  workspace sync and checks run without a later re-sync replacing it.

## 1.0.0rc2 - 2026-07-23

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
