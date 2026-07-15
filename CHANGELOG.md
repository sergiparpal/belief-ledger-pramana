# Changelog

## Unreleased

- Made terminal command strings fail closed; every terminal invocation now requires the
  effectful-action confirmation path.
- Added recursive structured secret redaction for credentials, headers, cookies, private keys,
  JWTs, common provider tokens, and credential-bearing URIs before evidence or gate data persist.
- Added schema migration 3 with HMAC-SHA-256 event authentication, a private 256-bit local key,
  and replay-time authentication checks. Restricted ledger state, configuration, and extensions
  to private profile-local paths with POSIX and Windows ACL validation.
- Hardened terminal classification, action-bound confirmation, request-bound context injection,
  and execution-output provenance.
- Made freshness, configuration controls, retraction acknowledgement, and linter citation
  enforcement operational rather than advisory.
- Fixed multi-event idempotency, verification-task races, source-stat deltas, and atomic LLM
  budget reservation; added schema migration 2.
- Added batching for belief hydration, stronger input validation, expanded regression/evaluation
  coverage, package smoke checks, and pinned audited runtime dependencies.

## 1.0.0rc1 - 2026-07-11

- Complete standalone Hermes `0.18.2` plugin with directory and entry-point discovery.
- Typed, episode-scoped, hash-chained event ledger with deterministic replay and purge compaction.
- Fixed-point defeat/reinstatement, conflicts, structural retractions, verification, and āpta updates.
- Lazy provenance-aware ingestion, qualified absence, bounded host-LLM components, and auditable verdicts.
- Per-provider-request context injection across four API shapes, final-output linting, and action gating.
- Offline Suites A-D, executable ablations, performance evidence, property/fuzz tests, and release tooling.

This is a local release candidate. No public GitHub or package-registry release has been made.
