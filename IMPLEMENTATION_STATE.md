# 1.0.0rc1 baseline implementation state

This file is the completed, frozen `1.0.0rc1` baseline. Post-baseline rearchitecture progress is
tracked separately in `IMPLEMENTATION_PLAN.md`; unchecked tasks there are not implied complete by
the phase table below.

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
