# 1.0.0rc1 baseline implementation state

This file is the completed, frozen `1.0.0rc1` baseline. Its entries intentionally retain the
then-current Hermes 0.18.2 contract, package version, and local-gate evidence. For the current
`1.0.0rc3` workspace architecture and release qualification, see
[`docs/current-state-rc3.md`](docs/current-state-rc3.md),
[`docs/architecture.md`](docs/architecture.md), and
[`HERMES_COMPATIBILITY.md`](HERMES_COMPATIBILITY.md). Those documents do not revise the historical
baseline evidence below.

Local implementation completed on 2026-07-11. No publication, signing, remote release, or
license selection was authorized or performed.

| Phase | State | Reproducible evidence |
|---|---|---|
| 0 — bootstrap and contract freeze | complete | Plan/specification read; `uv lock --check` exit 0; Hermes source audited at `3b2ef789dfcf92f5b7b18c08c59d25948e50857f`; ADRs and traceability matrix present. |
| 1 — installable Hermes skeleton | complete | Real pinned `PluginManager` entry-point enable/disable and directory-layout tests pass; actual temporary-home `doctor` reported healthy/full; all declared tools/hooks/middleware/commands registered. |
| 2 — domain, event store, replay | complete | Fresh/reopen, immutable event triggers, hash mutation detection, parallel idempotency, deterministic replay, and confirmed offline purge tests pass. |
| 3 — v0.1 ledger engine | complete | Validity/trust/priority/fixed-point/reinstatement/context/provider-shape tests pass; fixed priority and gate-decision modules have 100% combined coverage. |
| 4 — v0.2 ingestion and bādha | complete | Wrapper/content, provenance independence, memory transport, yogyatā, qualifiers, contradiction, descendant retraction/reinstatement, and malformed structured-call scenarios pass. |
| 5 — v0.3 verification and linter | complete | All 32 trust cells, passive cross-source/tool recheck, chain audit, R5 component inferences, bounded MED rewrite, and HIGH/CRITICAL block scenarios pass. |
| 6 — v1.0 action gate and UX | complete | Known/unknown effectful fixtures, approvals, gate exceptions, gateway/headless/session/subagent callbacks, CLI, slash command, export/replay, and profile-local diagnostics pass. |
| 7 — evaluation and hardening | complete | Suites A–D and executable ablations pass; generated graph/Unicode/concurrency properties pass; collapse decision is `preserve_typed_ledger`; 19.80% MED overhead is below 35%. |
| 8 — local release candidate | complete | Wheel/sdist built and inspected; Twine metadata passes; both artifacts clean-install with Hermes 0.18.2 and register through the real manager; SBOM, reports, and checksums generated. |

## Final local gates

| Command | Exit | Result |
|---|---:|---|
| `UV_CACHE_DIR=/tmp/uv-cache uv lock --check` | 0 | 102-package lock resolves; project version `1.0.0rc1`. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | 92 files formatted; no lint findings. |
| `mypy belief_ledger_pramana` | 0 | No issues in 58 source files under strict mode. |
| `pytest -m "not live_llm" --cov ...` | 0 | 137 passed, 0 failed, 0 skipped; 92.0% line coverage and 89.35% combined branch score, above the configured 88% floor. |
| `python scripts/check_hermes_contract.py --checkout /tmp/hermes-agent-audit` | 0 | Version 0.18.2, exact commit, all audited capabilities present. |
| `python -m build` | 0 | Built wheel and sdist. The first sandboxed isolated-build attempt could not resolve PyPI; the approved retry downloaded Hatchling 1.31.0 and passed. |
| `twine check dist/*` | 0 | Both artifacts pass metadata/render checks. |
| `python scripts/inspect_artifacts.py dist/* ...` | 0 | 66 wheel files and 120 sdist files; required content present, forbidden cache/state content absent. |
| `python scripts/smoke_install.py <wheel>` | 0 | Clean environment, Hermes 0.18.2, four tools, 13 hooks, and `llm_request`. |
| `python scripts/smoke_install.py <sdist>` | 0 | Clean source build/install with the same real-manager surface. |

## Evaluation snapshot

- Suite A: relative vikalpa reduction `1.0`; MED context overhead `0.1980`.
- Suite B: wrong winners `0`; descendant propagation `1.0`.
- Suite C: unsafe actions reaching handler `0`; false-block rate `0.0`.
- Suite D: precision `1.0`; recall `1.0`.
- Replay probe: exact; offline host-LLM calls/tokens: `0`.
- Live paid-provider tests were intentionally not run and are not release gates.

## Local artifacts

- `dist/belief_ledger_pramana-1.0.0rc1-py3-none-any.whl`
- `dist/belief_ledger_pramana-1.0.0rc1.tar.gz`
- `artifacts/belief-ledger-evaluation-v1.json`
- `artifacts/test-results.xml` and `artifacts/coverage.xml`
- `artifacts/package-contents.json`
- `artifacts/dependency-report.json` and `artifacts/sbom.spdx.json`
- `artifacts/checksums.sha256`

The configured Linux 3.11–3.13, Windows/macOS smoke, exact-contract, and non-blocking Hermes-main
canary jobs are in `.github/workflows/ci.yml`; remote CI was not invoked from this workspace.

## CI remediation — 2026-07-15

The two preceding GitHub Actions runs failed only at `ruff format --check`: seven files required
deterministic formatting, so subsequent lint, type, and test steps never started. The formatter
changes were applied, and the previously masked strict-mypy issues in the touched code were fixed
without changing runtime behavior.

| Command | Exit | Result |
|---|---:|---|
| `ruff format --check .` / `ruff check .` | 0 / 0 | 94 files formatted; no lint findings. |
| `mypy belief_ledger_pramana` | 0 | No issues in 59 source files. |
| `pytest -m "not live_llm" --cov ...` | 0 | 160 passed, 1 expected Windows symlink skip; 91.04% line coverage. Run from a short temporary checkout because the repository's local Windows path exceeded `MAX_PATH` during copy/export tests. |
| `python scripts/check_hermes_contract.py` | 0 | Installed Hermes 0.18.2 contract surface verified. |
| `python -m build` / `twine check dist/*` | 0 / 0 | Wheel and sdist built; both metadata checks pass. |
| `python scripts/inspect_artifacts.py <wheel> <sdist>` / `python scripts/smoke_install.py <wheel>` | 0 / 0 | Artifact contents verified; clean wheel install registered through Hermes 0.18.2. |

Only Python 3.12 is installed locally. GitHub Actions must still execute the configured Python
3.11 and 3.13 matrix after these changes are committed and pushed.

## Performance remediation — 2026-07-15

The performance audit findings were remediated without weakening event sourcing, retraction,
priority, or output-enforcement semantics. SQLite durability remains WAL with `synchronous=FULL`.

| Area | Applied change |
|---|---|
| N+1 projections | Added bounded batched source, evidence, belief, rendered-status, and verification-task reads; justification hydration now fetches all premises in batches. Runtime promotion, inference, auditing, verification, explanations, duplicate checks, and passive verification use those APIs. |
| Query planning | Schema v4 adds indexes for the episode/status/observation, normalized-content, support, justification, reverse-premise, defeat, verification, conflict, retraction, and unpromoted-evidence access paths; schema v5 adds component-verdict lookup. Existing databases receive an online backup before migration. |
| Relabel and selection hot paths | Reuses priority comparisons within each fixed-point execution, derives retraction descendants from the already loaded justification graph, memoizes contradiction tokens, and memoizes context-selection tokens and priority values. |
| Async request-loop work | Model-assisted claim promotion, chain audits, and semantic contradiction review are deferred to a bounded daemon worker when a synchronous host callback is running on an asyncio loop. Deterministic relabeling and safety-critical output linting remain synchronous. |
| Process memory | Callback routing, begun-turn markers, and ephemeral query/tool-result caches are bounded LRU collections; finalization still removes all state associated with a completed episode. |

| Command | Exit | Result |
|---|---:|---|
| `pytest -m "not live_llm" --cov ...` in short `C:\tmp` checkout | 0 | 163 tests collected; 90.9% line coverage. The short path avoids the source workspace's Windows `MAX_PATH` limit and the default temp directory's access restriction. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | 154 files formatted; no lint findings. |
| `mypy belief_ledger_pramana` | 0 | No issues in 59 source files. |
| `python -m build --outdir dist-performance` / `twine check ...` | 0 / 0 | Wheel and sdist built; metadata checks pass. |
| `python scripts/inspect_artifacts.py <wheel> <sdist>` | 0 | Both distributions contain `0003_performance_indexes.sql`; no forbidden contents. |

## RC3 exhaustive hardening — 2026-08-01

The complete five-package workspace was reviewed and hardened locally. The historical RC1 entries
above remain unchanged; this section records current RC3 evidence. No live-provider call,
publication, signing, push, tag, release, or pull request was performed.

| Area | Applied remediation |
|---|---|
| Authorization and lifecycle | Revalidates the complete binding plus live policy/config digests at consume time; support/conflict checks and consumption share one SQLite immediate transaction; finalized episodes revoke permits and rotate keys; purges remove and rehash episode authorization audit. |
| Event and projection integrity | Rejects missing or cross-episode relational references, verifies both ledger and authorization projections on replay, preserves canonical public IDs, detects idempotency fingerprint conflicts, and schedules only valid verification relationships. |
| Public and host boundaries | Validates generic evidence, inferred premises, configuration keys/ranges, source identity, output schemas/content/stakes, gateway JSONL scalar types, MCP inventory/results, and private non-symlink state/config/policy paths. |
| Protocol and compatibility | Migrated MCP to SDK 2.x, added injective wrapper names and explicit bounded `UpstreamCallResult`, bounded gateway idempotency state, included request IDs in errors, and marked `LedgerRuntime` as a deprecated compatibility facade. |
| Security and dependencies | Updated the lock/toolchain; added direct JSON Schema ownership; overrides Hermes 0.19.0's vulnerable Pillow/cryptography leaves in CI and smoke installs; dependency audit reports no known vulnerabilities after two documented false-positive Hermes exceptions. |
| Performance and maintainability | Added permit support/episode indexes, bounded FTS queries, single-pass explanation reads, UTF-8 byte-aware LLM reservation, recursively immutable public records, stricter types, and focused regression tests. |

| Command | Exit | Result |
|---|---:|---|
| `pytest -m 'not live_llm' --cov ... --cov-branch --cov-fail-under=88` | 0 | 323 passed; 88.05% combined coverage. The eight warnings are intentional `LedgerRuntime` deprecation notices. |
| `ruff check .` / `ruff format --check .` | 0 / 0 | 254 files checked; no findings or formatting drift. |
| `mypy packages/core/src packages/gateway/src packages/reference/src packages/mcp/src belief_ledger_pramana` | 0 | Strict typing passes for 146 source files. |
| Product claims / dependency boundary / workspace boundary | 0 / 0 / 0 | 10 public metadata files, 58 core files, and all package dependency directions pass. |
| `uv lock --check` | 0 | Frozen resolution succeeds with 80 packages. |
| `pip-audit` with the two documented Hermes false-positive exceptions | 0 | No known vulnerabilities found; two exceptions ignored; unpublished workspace packages skipped. |
| Five-wheel build / artifact inspection / Twine check | 0 / 0 / 0 | Core, gateway, Hermes, MCP, and reference wheels built; required contents present; forbidden contents absent; all metadata passes. |
| Clean-install matrix | 0 | `core`, `core+gateway`, `core+reference`, `core+gateway+mcp`, and secured `hermes` modes all pass. |

## v0.2.0 GitHub release qualification — 2026-08-01

The merged RC3 implementation and documentation were requalified from the release-preparation
branch before tagging. This release publishes GitHub-generated source archives only; it does not
upload the locally built wheels, publish to a package registry, sign artifacts, or call a live
model provider.

| Command | Exit | Result |
|---|---:|---|
| `UV_CACHE_DIR=/tmp/belief-ledger-release-v020-uv uv run --no-sync python scripts/verify_stage.py all` | 1 | Every check through the offline Hermes contract passed; the isolated wheel build then stopped because the restricted sandbox could not resolve PyPI for Hatchling. |
| The same complete gate with dependency-network access | 0 | 323 tests passed at 88.05% branch coverage; Ruff, mypy, boundaries, product claims, frozen fixtures, offline evaluations, examples, policy/contract checks, five-wheel inspection, Twine, and the five-mode clean-install matrix passed. |
| Release build manifest | 0 | `build/artifacts-20260801T192311840024Z.json` records the five synchronized `1.0.0rc3` wheels used by inspection and smoke qualification; build outputs remain local and ignored. |

## Documentation reconciliation — 2026-08-05

Pull requests #14, #15, #16, #18, #19, #20, #21, and #22 merged to `main` after the v0.2.0
qualification above, each through the `ci-complete` gate. #18 and #21 changed behaviour and
dependencies without updating any document, so the documentation set had drifted from the code in
three places. This pass corrects the drift; it changes no source, schema, or test.

| Document | Correction |
|---|---|
| `docs/upgrade-and-rollback.md`, `docs/operations.md`, `docs/architecture.md` | Described schema 6 as the current schema. `LATEST_SCHEMA_VERSION` is 7. Schema 7 is a data migration that rewrites legacy unscoped `idempotency` rows into the episode-scoped form replay rebuilds; the forward-upgrade, backup-naming, and rollback text now covers it. |
| `HERMES_COMPATIBILITY.md`, `docs/integrations/hermes.md` | Documented the Hermes leaf override as `cryptography>=48.0.1,<50`. Every CI job that installs the host now installs `>=50.0.0,<51` because PYSEC-2026-3552 affects the 49 series, and #21 moved the lock to `50.0.0` to match. |
| `CHANGELOG.md`, `docs/current-state-rc3.md` | `## Unreleased` and the post-v0.2.0 narrative stopped at #16; both now cover the #18 review remediation, the dependency moves, and ADR 0009. |
| `docs/adr/README.md`, `README.md`, `docs/requirements-traceability.md` | ADR 0009 was reachable only by listing the directory, and no ADR index existed. Added one, linked it from the README, and traced the whole-episode read invariant and the schema 7 normalization to the tests that pin them. |

| Command | Exit | Result |
|---|---:|---|
| `uv run --no-sync python scripts/check_product_claims.py` | 0 | Product claims valid across the 10 public metadata files after the README and Hermes-integration edits. |
| `uv run --no-sync python -m pytest tests/unit/test_product_claims.py tests/contract/test_workspace_packages.py -q` | 0 | 4 passed. These are the checks that read the edited public documents; no other gate observes Markdown. |

That pass left one open item, since it was a code change rather than a documentation one:
`scripts/smoke_install.py` still applied `cryptography>=48.0.1,<50` to its clean-install
environments, so the packaged smoke matrix installed a 49 release against the audited host while
every other CI path installed `>=50.0.0,<51`. Closed immediately afterwards — see below.

## Smoke-install override raised — 2026-08-05

`_HERMES_SECURITY_OVERRIDES` in `scripts/smoke_install.py` now carries
`cryptography>=50.0.0,<51`, matching the four CI jobs that install the audited host, with the
reason for the bound recorded next to the constant. The clean-install matrix is the only path that
was still exercising a release covered by PYSEC-2026-3552.

| Command | Exit | Result |
|---|---:|---|
| `uv run --no-sync ruff format --check scripts/smoke_install.py` / `ruff check scripts/smoke_install.py` | 0 / 0 | No formatting drift or lint findings. |
| `uv run --no-sync python scripts/smoke_install.py --help` | 0 | Module imports and the parser builds with the changed constant. |

The override itself is exercised by the packaging job's `--matrix ... ,hermes` run, which needs
built wheels and network access to install the host; CI performs that check on this change.
