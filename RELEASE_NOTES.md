# Belief Ledger Pramana v0.1.3

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
