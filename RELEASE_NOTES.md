# Belief Ledger Pramana 1.0.0rc2 (unreleased)

This candidate repositions the project as evidence-backed policy enforcement for AI agents,
extracts a host-neutral core, and adds a strict standalone reference adapter while preserving the
Hermes installation and state paths. Enforcement guarantees are now capability-profiled; no
package has been published by this repository work.

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
artifact jobs. Package and release publication remain deliberately unperformed.

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
