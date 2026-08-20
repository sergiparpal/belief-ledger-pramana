# 1.0.0rc1 baseline implementation state

This file is the completed, frozen `1.0.0rc1` baseline. Its entries intentionally retain the
then-current Hermes 0.18.2 contract, package version, and local-gate evidence. For the current
`1.0.0rc4` workspace architecture and release qualification, see
[`docs/architecture.md`](docs/architecture.md), [`RELEASE_NOTES.md`](RELEASE_NOTES.md), and
[`HERMES_COMPATIBILITY.md`](HERMES_COMPATIBILITY.md). Those documents do not revise the historical
baseline evidence below. Sections below that mention `docs/current-state-rc3.md` or
`docs/current-state-rc4.md` are historical: that document was removed on 2026-08-19 because it was a
fourth rendering of the release narrative, and what it said now lives in `RELEASE_NOTES.md`,
`CHANGELOG.md`, and `docs/obvious-fix-report.md`.

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
| `CHANGELOG.md`, `docs/current-state-rc4.md` (then `-rc3`) | `## Unreleased` and the post-v0.2.0 narrative stopped at #16; both now cover the #18 review remediation, the dependency moves, and ADR 0009. |
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

## v0.2.1 GitHub release qualification — 2026-08-05

The five synchronized workspace distributions advanced from `1.0.0rc3` to `1.0.0rc4` because their
code changed after v0.2.0; the product surface itself is unchanged. `docs/current-state-rc3.md` was
renamed to `docs/current-state-rc4.md` and rewritten from "unreleased corrections on `main`" into
the released state. This release publishes GitHub-generated source archives only; it does not upload
the locally built wheels, publish to a package registry, sign artifacts, or call a live model
provider.

| Command | Exit | Result |
|---|---:|---|
| `uv lock --check` | 0 | Frozen resolution succeeds with 80 packages at the bumped versions. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | 257 files formatted; no lint findings. |
| `mypy packages/core/src packages/gateway/src packages/mcp/src packages/reference/src belief_ledger_pramana` | 0 | Strict typing passes for 146 source files. |
| `pytest -m "not live_llm" --cov ... --cov-branch` | 0 | 353 passed; 88.18% combined coverage against the 88% floor. Ten warnings: the intentional `LedgerRuntime` deprecations and two SQLite `ResourceWarning`s. |
| Dependency boundary / workspace boundary / product claims | 0 / 0 / 0 | All pass after the version bump and the release-document edits. |
| Examples, gateway demo, offline Suites A–E, policy validate | 0 | Evaluation report passes; policy validates at normalized schema 2. |
| `scripts/check_hermes_contract.py --allow-missing` | 0 | Installed host reports 0.19.0 with every audited capability present. CI's `exact-hermes-contract` job pins the commit against a checkout. |
| Five-wheel build / artifact inspection | 0 / 0 | `build/artifacts-20260805T215737968413Z.json` records the five `1.0.0rc4` wheels; required contents present, forbidden contents absent. |
| `twine check` on the five wheels | 0 | All five PASSED. Run through `uvx --from twine==7.0.0`, see below. |
| `scripts/smoke_install.py --matrix core,core+gateway,core+reference,core+gateway+mcp,hermes` | 0 | All five clean-install modes pass and report `1.0.0rc4`. |
| `pip-audit --ignore-vuln CVE-2026-10221 --ignore-vuln CVE-2026-10224` | 0 | No known vulnerabilities; the five unpublished workspace packages are skipped. |

One gate run failed before this one and neither failure was a defect in the release:

- `test_corrupted_unicode_tool_results_remain_bounded_and_replayable` raised `FlakyFailure`: the
  first call took 345.69 ms against Hypothesis' 200 ms default deadline and 166.45 ms on retry. The
  property held; only the timing gate failed. Both ledger-backed property tests now set
  `deadline=None`, since each example drives real SQLite work and the gate runs them under coverage
  instrumentation. No assertion was weakened.
- `python -m twine check` could not import: `hermes-agent` downgrades `packaging` to 26.0 in the
  shared local venv and twine 7 needs `packaging.errors` from 26.2, which the lock pins. CI never
  hits this because `all-wheel-artifacts` does not install the host. Re-run in isolation with
  `uvx --from twine==7.0.0 twine check build/workspace-*/*.whl`, which passed. `scripts/verify_stage.py`
  still invokes the venv's twine, so the complete local gate cannot finish in a venv that also has
  Hermes installed.

## Obvious-fix plan, Stage 1 — documentation invariant guard — 2026-08-10

`scripts/check_doc_invariants.py` is a new, separate checker from
`scripts/check_product_claims.py`: that one guards restricted marketing language, this one guards
derived facts. Each fact names one source of truth read with `ast` or `tomllib`, never by importing
the module, and lists the documents that must state it together with the pattern each states it in.
A listed document that matches the pattern nowhere fails exactly as loudly as one that matches with
a stale value — the drift found on the first run was absence, not staleness, in all three cases.

Drift found and corrected in this change:

| File | What was wrong |
|---|---|
| `docs/operations.md` | Discussed schema v6 and v7 by name but never stated which version is current |
| `docs/architecture.md` | Same |
| `README.md` | Never stated the `requires-python` range; only `HERMES_COMPATIBILITY.md` did |

| Command | Exit | Result |
|---|---:|---|
| `python scripts/check_doc_invariants.py` | 0 | 6 facts across 9 files; every schema version has a migration. |
| `python scripts/check_doc_invariants.py --root <mutated tree>` | 1 | Names fact, expected value, file, line and found value. |
| `pytest tests/unit/test_doc_invariants.py` | 0 | 14 passed, including seven that prove the checker fails. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 367 passed, 88.16% combined coverage against the 88% floor. |

Test count moved 353 → 367. Coverage is unchanged at 88.16%: the checker lives in `scripts/`, which
is outside the five measured packages, and the tests that exercise it are test code.

## Obvious-fix plan, Stage 2 — priority claim reconciliation — 2026-08-10

Q1 answered A: keep the behaviour, correct the claim. No runtime code changed; `engine/priority.py`
gained a module docstring, the specification's §1 and §4.2 were made precise, and
`tests/unit/test_priority_order.py` pins the order structurally so the claim and the tuple cannot
diverge again. Recorded as [ADR 0010](docs/adr/0010-scalar-competence-in-the-priority-order.md).

Writing the pinning test found something neither document stated: `_type_key` bands SHABDA into
`shabda_apta_hi`/`_mid`/`_lo` using the same `effective_competence` scalar, so competence also moves
`type_rank`. The first version of the "reliability decides the tie" test failed for exactly that
reason — a 0.9-vs-0.2 contest between two testimony beliefs resolves at `type`, not `reliability`.
The docstring, the specification and the ADR all state the coupling rather than eliding it, and
`test_for_shabda_a_competence_gap_across_a_band_boundary_is_decided_at_type` keeps it stated. Logged
as F-07.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_priority_order.py` | 0 | 12 passed. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | 265 files, no findings. |
| `mypy packages/{core,gateway,mcp,reference}/src belief_ledger_pramana` | 0 | 146 source files. |
| `scripts/check_doc_invariants.py` / `scripts/check_product_claims.py` | 0 / 0 | No drift, no restricted claims. |
| `pytest -m "not live_llm" --cov ... --cov-branch` | 0 | 379 passed; 88.18% against the 88% floor. |

Test count moved 367 → 379; coverage 88.16% → 88.18%.

## Obvious-fix plan, Stage 2b — self-claim scope guard — 2026-08-10

Tracing the call site answered the plan's branch: `is_about_user_self` is reached only from
`ingest_user_message`, whose source is always `user_source(...)` and therefore always
`SourceKind.USER`, and that method is called only from the `pre_llm_call` hook with
`kwargs["user_message"]`. Content from a tool result, a fetched page, a file, or a prior-ledger
belief cannot reach it. The guarantee held, but it held by call-site placement, which nothing
enforced.

`is_user_self_claim(source, content)` now enforces it: a non-`USER` source is refused before the
pattern is consulted. `trust_profile` already gated the `user_self` branch on the same kind, so the
privilege now has two independent guards and removing either alone fails a test.
`is_about_user_self` is unchanged and still exported, so the compatibility surface only grows.

The privilege itself is not what the plan described. `about_self` selects the `user_self` trust
profile over `user_world`; at HIGH stakes that is `svatah`/`k=0` against `paratah`/`k=1`
`cross_source`. It waives cross-source verification rather than raising a competence scalar. Logged
as F-08, with the unreachable `self: 0.95` competence entry as F-09 and the pattern's limitations
as F-10.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_self_claim_scope.py` | 0 | 26 passed: 12 scope pins, 11 characterisation cases, 3 profile assertions. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | 266 files, no findings. |
| `mypy packages/{core,gateway,mcp,reference}/src belief_ledger_pramana` | 0 | 146 source files. |
| `scripts/check_product_claims.py` / `scripts/check_doc_invariants.py` | 0 / 0 | Threat-model addition passes the restricted-language checker. |
| `pytest -m "not live_llm" --cov ... --cov-branch` | 0 | 405 passed; 88.18% against the 88% floor. |

Test count moved 379 → 405; coverage unchanged at 88.18%.

## Obvious-fix plan, Stage 3 — recency for slow and stable beliefs — 2026-08-10

R1 from the baseline said replay-independent, so this proceeded without a fixture copy:
`tests/fixtures/v1_replay/` is untouched and still verifies byte-for-byte. `recency_rank` is now
computed from `observed_at` for every perishability class and stays the fifth key, which bounds the
change by position rather than by a guard.

Making recency unconditional made `_timestamp`'s naive-datetime raise reachable for every belief,
so the guarantee moved to `Belief.__post_init__`, alongside `parse_datetime` and `FixedClock` which
already enforce it with the same message. `_timestamp`'s check is kept as redundancy and is pinned
so it is not read as dead code.

Running the suite before adding new tests produced exactly one failure — the naive-construction
test — and no defeat outcome moved anywhere in `tests/` or `evaluations/`. The case this change
exists to fix was not covered by any existing test or suite. Logged as F-11; the changed test is
F-12.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_recency_priority.py` | 0 | 14 passed, including the relabel pair that resolves and the same-timestamp control that still reaches saṃśaya. |
| `pytest tests/contract/test_v1_replay.py` | 0 | 10 passed; frozen projection hashes unchanged. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 419 passed; 88.18% against the 88% floor; evaluations, examples, gateway demo, policy validate and the Hermes contract all pass. |

Test count moved 405 → 419; coverage 88.18%, equal to the Stage 0 baseline of 88.16% and above the
floor. Removing the perishability branch and adding the constructor branch net out.

## Obvious-fix plan, Stage 4 — model determinism and divergence auditability — 2026-08-10

Neither of the plan's Part A branches matched. The port could not carry sampling parameters, but
`hermes/model_port.py` and `belief_ledger_pramana/llm/client.py` were each already passing
`temperature=0.0` to the Hermes facade as a literal. Sampling was controlled — twice, invisibly,
with nothing linking the two or recording what was applied. `SamplingPolicy` now expresses it once,
`verification.sampling_temperature` configures it with bounded validation in both validators, and
it rides on `StructuredModelRequest` as an additive defaulted field so existing port
implementations are unaffected. Logged as F-16.

Attribution is a new `LLM_CALL_ATTRIBUTION` record written alongside the usage and verdict records.
R2 from the baseline confirmed neither `ComponentVerdict` nor `LlmUsage` can take a required field
without moving a frozen v1 hash, so the sibling-record option the plan marks preferred is the one
taken. No projection table is added, so neither projection hash moves either. `prompt_hash` digests
the prompt text because the prompt module carries no version to reuse (F-14).

`hermes belief-ledger llm-divergence` groups by `(prompt_hash, input_hash)` and reports every input
answered more than one way. Failed calls are excluded.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_llm_divergence.py` | 0 | 16 passed, including the two-different-outputs acceptance case and the identical-results control. |
| `pytest tests/integration/test_operator_surfaces.py` | 0 | 6 passed; the CLI reports one divergent group and nothing for a clean episode. |
| `pytest tests/contract/test_v1_replay.py` | 0 | 10 passed; frozen event and projection hashes unchanged. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | No findings. |
| `mypy packages/{core,gateway,mcp,reference}/src belief_ledger_pramana` | 0 | 150 source files. |
| `pytest -m "not live_llm" --cov ... --cov-branch` | 0 | 436 passed; 88.33% against the 88% floor. |

Test count moved 419 → 436; coverage 88.18% → 88.33%.

## Obvious-fix plan, Stage 5 — external anchoring of the hash chain — 2026-08-10

Q2 answered A: a local append-only JSONL sink, no HTTP adapter. The integrity key sits beside the
database, so an attacker who can read the ledger can also forge it: edit a row, re-chain everything
after it, and `db verify-chain` passes because the chain is internally consistent again. An anchor
is a copy of the chain root written where the ledger cannot reach back into.

The plan's instruction to reuse `chain_audit.py`'s chain-state computation did not apply — that
module audits *inference* chains, not the hash chain (F-17). The instruction's intent was followed
against `LedgerStore.verify_hash_chain`, whose head computation moved into `_verified_heads` so
`chain_state(up_to_height=...)` shares it. There is still exactly one root computation, which is
what keeps an anchor mismatch from being ambiguous between tampering and a bug.

The acceptance test is the tamper simulation and it is unconditional: build a real chain from a
frozen fixture, publish an anchor, drop the append-only triggers, edit event 2, faithfully re-chain
everything after it, restore the triggers, then assert `verify_hash_chain` still passes and anchor
verification fails at the anchored height naming both roots. Dropping the triggers is faithful to
the threat: a trigger is a row in the schema of a file the attacker can write (F-18).

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_chain_anchoring.py` | 0 | 17 passed: tamper simulation, unreachable-height case, append-only and 0600 sink, path validation, no key material in any record. |
| `pytest tests/integration/test_operator_surfaces.py` | 0 | 7 passed; `anchor publish`/`verify` and `db verify-chain --against-anchors` exercised through the CLI. |
| `python scripts/check_product_claims.py` | 0 | The anchoring documentation passes the restricted-language checker without relaxing it. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 454 passed; 88.39% against the 88% floor. |

Test count moved 436 → 454; coverage 88.33% → 88.37%. Warnings stay at 8: the raw `sqlite3`
connections the tamper helper needs are closed explicitly, so no `ResourceWarning` is added.

## Obvious-fix plan, Stage 6 — snapshots as a discardable cache — 2026-08-10

Schema 8 adds the `snapshots` table. It adds no event kind and no projection table, so
`projection_hash_v1` and `projection_hash_v2` are both unchanged and the frozen v1 fixtures still
verify byte-for-byte. Rolling back to schema 7 loses nothing but the cache.

The four invariants are tests, not comments. Deleting every snapshot loses nothing, asserted against
all six frozen fixtures. A stale derivation fingerprint means discard rather than upgrade, and so
does a corrupt payload — `zlib.decompress` runs before the content hash can be checked, so it needed
its own guard, which is F-21. `db replay` with no flags reads every event, asserted by event-read
count rather than timing. `db verify-snapshot` rebuilds twice and compares every projection table
row by row.

`replay.max_events_warn` defaults to 50 000. The v1 fixtures are 22 events, so no timing measurement
in this repository can surface the scaling wall — the threshold is a configured number for that
reason.

Two existing tests changed. One hardcoded the schema numbers in the migrate dry-run output (F-19).
The other rolled the schema stamp back by `LATEST_SCHEMA_VERSION` to re-run the v7 idempotency
rescoping, which silently stopped re-running it the moment v8 existed (F-20) — a latent defect in
the test that schema 8 revealed rather than caused.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_snapshots.py` | 0 | 29 passed, one per invariant plus the parametrized sweep over all six fixtures. |
| `pytest tests/properties/test_ledger_properties.py` | 0 | 6 passed; the new hypothesis case snapshots at a generated height and asserts the accelerated rebuild equals the full one. |
| `pytest tests/contract/test_v1_replay.py` | 0 | 10 passed; frozen hashes unchanged across the schema bump. |
| `python scripts/check_doc_invariants.py` | 0 | The Stage 1 guard forced the schema bump into all three documents in the same change. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 484 passed; 88.31% against the 88% floor. |

Test count moved 454 → 484; coverage 88.37% → 88.31%. The drop is the new
`from_snapshot`/verification branches in `store.py` and `snapshots.py`, which are exercised but not
saturated; both remain well above the floor and above the 88.16% Stage 0 baseline.

## Obvious-fix plan, Stage 7 — structural consolidation — 2026-08-10

**7a.** `tests/fixtures/compat_surface.json` and `tests/unit/test_compat_surface.py` pin the
`belief_ledger_pramana` import surface: 81 modules, 288 exported names. Nothing asserted this
before (F-03). It lives in `tests/unit/` rather than `tests/core/` because the `core-no-adapters`
CI job runs `tests/core` against a core-only venv that does not have the adapter installed.

**7b.** Packaged policy data now has one home. The adapter loads `belief_ledger_core.data`, the
three duplicated YAML files are deleted, and the byte-identity test became a one-copy assertion.
Re-export shims were kept: only four names are formally promised, so deleting everything outside
the promised surface would delete most of a 1.x compatibility contract (F-24).

**7c.** Q3 answered A. The facade keeps its warning, now pinned by a test, and removal is
documented for 2.0.0. Its callers were not migrated because `LedgerRuntime` is a fixture, not a
`BeliefLedger` wrapper (F-22).

**7d.** `runtime.py` (3,233 lines) is now a package, by pure moves:

| Module | Lines |
|---|---:|
| `runtime/__init__.py` | 42 |
| `runtime/errors.py` | 36 |
| `runtime/helpers.py` | 271 |
| `runtime/plugin_runtime.py` | 598 |
| `runtime/episode_service.py` | 2430 |

The 600-line target is not met and cannot be met by pure moves: `EpisodeService` is one class of
2,430 lines, and splitting a class is not a move (F-23). The guard ships anyway, with eight
exemptions that each record a ceiling and a reason, plus tests that stop an exempt file growing and
force a file that drops under the limit to leave the list.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_compat_surface.py` | 0 | 90 passed; no module or exported name lost across the split. |
| `pytest tests/unit/test_architecture.py` | 0 | 9 passed, including the four size-guard tests. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | No findings after the move. |
| `mypy packages/{core,gateway,mcp,reference}/src belief_ledger_pramana` | 0 | 158 source files. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 581 passed; 88.42% against the 88% floor. |

Test count moved 484 → 581; coverage 88.31% → 88.42%. Largest source file: 3,233 → 2,430 lines.

## Obvious-fix plan, Stage 8 — consolidation and reporting — 2026-08-10

Final gate from the Stage 7 tree, `docs/obvious-fix-report.md` written, and
`docs/current-state-rc4.md` updated with the post-plan state.

| Command | Exit | Result |
|---|---:|---|
| `python scripts/verify_stage.py all --skip-build` | 0 | 581 passed; 88.46% against the 88% floor; 34.4 s wall clock. |
| `pytest tests/contract/test_v1_replay.py` | 0 | 10 passed; frozen hashes unchanged across a schema bump, a new record kind, a defeat-semantics change and the runtime split. |
| `git diff main -- tests/fixtures/v1_replay/` | — | Empty. The fixtures were never touched. |
| `python scripts/check_doc_invariants.py` | 0 | 6 facts across 9 files. |
| `python scripts/check_product_claims.py` | 0 | 10 public metadata files. |

Baseline → final: tests 353 → 581, coverage 88.16% → 88.46%, largest source file 3 233 → 2 430
lines, ADRs 9 → 15, warnings 8 → 8. One in-scope item is incomplete and listed with its reason: the
600-line target in Stage 7d, blocked by `EpisodeService` being a single 2,430-line class that no
pure move can divide (F-23).

## Deflake the semantic-contradiction integration test — 2026-08-16

`hermes-adapter (3.12)` failed on the Dependabot ruff PR (#29) while the identical commit passed in
the sibling run. The cause is not the dependency bump and not the adapter: it is a wall-clock
dependence that ADR 0011 introduced into
`tests/integration/test_semantic_contradiction.py`.

`priority_trace` computes the fifth key as `int(observed_at.timestamp())`, truncated to whole
seconds. The test ingests two contradicting user claims milliseconds apart through `utc_now()`.
Landing inside one second makes the pair tie on all five keys — saṃśaya, both PENDING, which is what
the test asserted. Straddling a second boundary lets recency settle the contest, so the older claim
goes OUT and the assertion fails. ADR 0011's "every existing test produced the same outcome before
and after" held only because the verifying run stayed inside one second; this pair is exactly the
stale-versus-fresh shape the ADR says must resolve to one IN and one OUT.

The clock is now pinned through `monkeypatch` on `episode_service.utc_now`, and both regimes are
asserted instead of whichever the clock supplies: thirty seconds apart resolves IN/OUT per ADR 0011,
one timestamp stays saṃśaya. The two REBUT edges and the R5 verdict — the behaviour the test is
named for — were invariant across every timing regime and are asserted in both.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/integration/test_semantic_contradiction.py` × 30, varying `PYTHONHASHSEED` | 0 | 0 failures; was reproducible under a forced boundary straddle before the fix. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | No findings. |
| `mypy packages/{core,gateway,mcp,reference}/src belief_ledger_pramana` | 0 | 158 source files. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 583 passed; 88.44% against the 88% floor. |
| `python scripts/check_doc_invariants.py` | 0 | 6 facts across 9 files. |
| `python scripts/check_product_claims.py` | 0 | 10 public metadata files. |

Test count 581 → 583; the split adds the saṃśaya control at integration level. Engine code is
untouched: changing the recency granularity from seconds to full precision would be a semantic
deviation and needs its own ADR. Left open, since second-granularity truncation still makes the
outcome for two claims under a second apart depend on where the boundary falls — see the note in
the test docstring.

## Full-precision recency key (ADR 0016), closing #31 — 2026-08-19

The item left open by the deflake above. `priority_trace` computed the fifth key as
`int(observed_at.timestamp())`, so `recency_rank` sat on a fixed one-second grid and whether two
beliefs tied depended on where a boundary fell rather than on how far apart they were — 2 ms across
a boundary resolved, 998 ms inside one second produced saṃśaya and two `PENDING` beliefs. `PENDING`
has no active exit, which is the harm ADR 0011 was written to close; truncation reintroduced it
non-deterministically for any pair observed inside one second.

The key is now whole microseconds from integer arithmetic on the `timedelta`. `datetime` resolves to
one microsecond, so the conversion is lossless and a tie means one instant.

`int(observed_at.timestamp() * 1_000_000)` — the obvious spelling, and the one the issue suggested —
was measured and rejected. float64 spacing at the current epoch is ~0.24 µs and grows with the date,
so it collapses adjacent microseconds back into ties: 0% of 100 000 pairs at 2026 and 2100, 9.7% at
2038, 50% at 2260, and it differs from the true microsecond count by 1 µs in ~10% of 100 000 random
timestamps across 1971–2260. Today's tests would be green and the artifact would return in 2038.
`test_distinct_instants_never_share_a_rank_at_any_epoch` is parametrized over those epochs and was
confirmed to fail at 2038 and 2260 under the float route.

Both regression tests were verified against the defects they guard rather than assumed to work:
reinstating second-truncation fails `test_a_sub_second_gap_decides_wherever_the_second_boundary_falls`
at `[inside-one-second]` and `[one-microsecond]`, and substituting the float route fails
`test_distinct_instants_never_share_a_rank_at_any_epoch` at exactly `[2038]` and `[2260]`.

`_timestamp` becomes `_recency_micros`, carrying the same timezone guard and message; ADR 0011's
defence-in-depth argument for it is unchanged and still pinned. Two consequences the issue did not
raise are recorded in ADR 0016: `context/select.py` sorts the bounded context window on the same
tuple, so same-second beliefs previously fell through to `belief.id` and are now ordered by
observation time; and `recency_rank` is rendered by `queries.explain` through the Hermes tool and
slash command, changing magnitude from ~1.79e9 to ~1.79e15 with no schema, fixture or contract
pinning it.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_recency_priority.py` | 0 | 21 passed, up from 14. |
| `pytest tests/` under both rejected implementations | 1 | Confirms each new test fails on the defect it guards; see above. |
| `ruff format --check .` / `ruff check .` | 0 / 0 | 289 files formatted; no findings. |
| `mypy packages/{core,gateway,mcp,reference}/src belief_ledger_pramana` | 0 | 158 source files. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 590 passed; 88.45% against the 88% floor; evaluations A–E `passed: true`. |
| `python scripts/check_doc_invariants.py` | 0 | 6 facts across 9 files. |
| `python scripts/check_product_claims.py` | 0 | 10 public metadata files. |

Test count 583 → 590: three sub-second boundary cases and four epoch cases. The only pre-existing
assertion that changed is `test_recency_is_computed_for_every_perishability_class`, which pinned
`int(FRESHER.timestamp())` — the truncation itself rather than a behaviour — and now pins the
microsecond count derived independently of the engine's arithmetic. The v1 replay contract stays
green and `tests/fixtures/v1_replay/` is untouched, for the reason ADR 0011 records: relabel output
is materialised into events and replay reapplies them rather than re-running the engine. The offline
evaluation report is identical to the baseline except for timestamps and timing measurements.

## Documentation consolidation — 2026-08-19

Removes five documents and merges what only they carried, after an inbound-reference sweep over
every tracked Markdown file. No source, schema, test, or fixture changed; the gate below is the
full one regardless, because two of the removed documents are cited by ADRs.

| Document | Disposition | Evidence for the call |
|---|---|---|
| `docs/baseline-v1rc1.md` | removed | Zero inbound references from any document, script, test, or CI job. Frozen 2026-07-22 against Hermes 0.18.2, schema 2 and `1.0.0rc2`; the live equivalents are `HERMES_COMPATIBILITY.md`, `docs/compat-surface.md`, and the rc1 sections of this file. |
| `docs/event-format.md` | merged into `docs/event-compatibility.md`, then removed | Zero inbound references; the README already sends the reader to `event-compatibility.md` for the same subject. The envelope example, the per-episode head note, the `event_auth`/HMAC paragraph, and the event-family inventory moved across; the two sentences that restated canonical-JSON and hash-material rules were dropped because the destination already stated both. |
| `docs/obvious-fix-baseline.md` | merged into `docs/obvious-fix-report.md` as an appendix, then removed | The report already compared against it section by section. ADRs 0010, 0011 and 0012 cite its R1 and R2 experiments as evidence, so the appendix keeps them and those three ADRs now point at it. |
| `docs/adapter-authoring.md` | merged into `docs/adapter-conformance.md`, then removed | One inbound reference (`packages/reference/README.md`), and the document opened by directing the reader to the file it is now part of. |
| `docs/current-state-rc4.md` | removed | A fourth rendering of the release narrative alongside `CHANGELOG.md`, `RELEASE_NOTES.md` and this file, and the only one no check guards. Measured with 8-word shingles, 12.2% of it is literal `RELEASE_NOTES.md` text and the rest restates the same facts. Its two live inbound links are redirected; the mentions in the historical sections above are left as history and flagged in this file's header. |

| Command | Exit | Result |
|---|---:|---|
| `python scripts/check_doc_invariants.py` | 0 | 6 facts across 9 files; none of the removed documents carried a guarded fact. |
| `python scripts/check_product_claims.py` | 0 | 10 public metadata files. |
| `pytest tests/unit/test_doc_invariants.py tests/unit/test_product_claims.py tests/unit/test_compat_surface.py tests/contract/test_workspace_packages.py` | 0 | 108 passed. These are the checks that read Markdown. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 603 passed; 88.46% against the 88% floor; evaluations A–E `passed: true`. |

Relative-link sweep over all tracked Markdown: no broken `](*.md)` link remains. Six backtick
mentions of `docs/current-state-rc3.md` and `docs/current-state-rc4.md` survive in the historical
sections of this file, which is the existing convention here — the rc3 mention was already
dangling on `main` after that document was renamed.

## CI Hermes checkouts, dependency bumps, and two corrected counts — 2026-08-20

Five commits landed after the documentation consolidation and none of them was recorded here: four
Dependabot bumps and the fix for the CI failure the first of those bumps was blamed for. No source,
schema, test, or fixture changed in any of them, and none changed a distribution version.

`exact-hermes-contract` is one of the eleven jobs in `ci-complete`'s `needs:` list, and its first
step after the sync was an unauthenticated `git clone` of `NousResearch/hermes-agent`. Anonymous
clones share the runner pool's IP rate limit, so github.com answered HTTP 429 and failed that
required job on `0c04755` — a commit whose own pull-request run had been green, and whose content
was a `setup-uv` bump that never touched the clone. A required job failing for a reason outside the
change under test is indistinguishable, at the branch ruleset, from a real regression.

Both external clones now go through the already-pinned `actions/checkout`, which authenticates the
fetch with the workflow token, retries a throttled response, and transfers only the commit being
audited instead of the whole history. `persist-credentials: false` keeps the token out of the
throwaway checkout. `hermes-main-canary` moved the same way: it is `continue-on-error`, so a 429
there costs signal rather than a red build, but a canary that flakes is not a canary.

The contract itself did not move. `scripts/check_hermes_contract.py` still pins
`AUDITED_VERSION = "0.19.0"` and `AUDITED_COMMIT = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"`, and
a depth-1 checkout of that commit still reports the audited commit, the version, and all eight
source-mode capabilities.

| Bump | From | To | Scope |
|---|---|---|---|
| `astral-sh/setup-uv` | 9.0.0 | 10.0.1 | CI action, re-pinned by SHA in the twelve jobs that use it |
| `mypy` | 2.3.0 | 2.3.1 | dev group |
| `hypothesis` | 6.165.2 | 6.165.10 | dev group |
| `types-pyyaml` | 6.0.12.20260724 | 6.0.12.20260815 | dev group |

Every count the consolidation entry states was re-measured in the same pass, and two of them were
wrong. Both errors were in the arithmetic, not the prose: each document already listed all five
removed files by name.

| Claim | Was | Is | How it was measured |
|---|---|---|---|
| Documents removed by the consolidation | "four" in this file | five | `git show 05dc49f --diff-filter=D --name-only` lists five `.md` deletions. |
| Tracked Markdown after the consolidation | "53" in `CHANGELOG.md` | 52 | `git ls-tree -r --name-only 05dc49f \| grep -c '\.md$'`, and `git ls-files '*.md' \| wc -l` at HEAD both return 52. |

One count that looks stale is not. O-22 in `docs/open-findings.md` cites "over 58 commits" as the
evidence for no external review, and the graph is at 65. It was left alone: that file pins every
measurement in it to commit `c1c8bdf`, and `git log --oneline c1c8bdf | wc -l` is exactly 58. The
author set it reports is also unchanged — `Sergi Parpal`, `sergiparpal`, `dependabot[bot]` — and
nothing between `c1c8bdf` and HEAD touched code, so the baseline still holds and refreshing one
figure inside it would have broken the convention that makes the rest re-checkable.

| Command | Exit | Result |
|---|---:|---|
| `pytest tests/unit/test_doc_invariants.py tests/unit/test_product_claims.py tests/unit/test_compat_surface.py tests/contract/test_workspace_packages.py` | 0 | 108 passed. These are the checks that read Markdown. |
| `python scripts/check_doc_invariants.py` | 0 | 6 facts across 9 files; no documented constant moved, which is why neither count error was caught by it. |
| `python scripts/check_product_claims.py` | 0 | 10 public metadata files. |
| `python scripts/verify_stage.py all --skip-build` | 0 | 603 passed; evaluations A–E `passed: true`. Run three times across the edits: 88.46%, 88.46%, then 88.47% against the 88% floor, so the combined figure is not bit-stable run to run. Test count and evaluation results match the pre-change baseline, as expected for a documentation-only change. |

Relative-link sweep over all 52 tracked Markdown files: every relative Markdown link resolves to a
file that exists. A naive regex sweep reports one non-resolving match, and it is not a link: it is
the inline-code fragment in which the consolidation section above quotes the link syntax itself.

Not recorded as a finding: `scripts/check_doc_invariants.py` guards six derived constants, and
neither corrected count is derivable from code — both are properties of a past commit. Nothing
mechanical would have caught them, and the check that did was re-measuring the claim.
