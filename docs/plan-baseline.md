# Obvious-fix plan — Stage 0 baseline

The measured state of the repository before any stage of
`belief-ledger-pramana-obvious-fixes-plan.md` was implemented. Every later stage reports its
numbers against this file. A stage that reduces a number here without an explanation in
[the findings register](plan-findings.md) is a failed stage.

## Commit

| Item | Value |
|---|---|
| Baseline commit | `3d21ccddce66a9659f3509cc1b5758a025541c52` |
| Branch | `plan/stage-0-baseline` off `main` |
| Date measured | 2026-08-10 |
| Python | 3.13 (workspace `.venv`) |
| Hermes host | `hermes-agent==0.19.0` installed as a peer |

## Gate results

| Check | Command | Exit | Result |
|---|---|---|---|
| Tests | `pytest -m "not live_llm"` | 0 | 353 passed, 0 failed, 0 skipped, 8 warnings |
| Coverage | `--cov-branch --cov-fail-under=88` | 0 | 88.2% total |
| Format | `ruff format --check .` | 0 | clean |
| Lint | `ruff check .` | 0 | clean |
| Types | `mypy packages/{core,gateway,reference,mcp}/src belief_ledger_pramana` | 0 | clean |

Coverage detail, from the CI invocation
(`--cov=belief_ledger_core --cov=belief_ledger_gateway --cov=belief_ledger_mcp
--cov=belief_ledger_pramana --cov=belief_ledger_reference --cov-branch`):

| Metric | Value |
|---|---|
| Statements | 9495 |
| Statements missed | 857 |
| Branches | 2818 |
| Partial branches | 495 |
| Total | 88.2% |

The eight warnings are all intentional `DeprecationWarning`s from tests that deliberately exercise
the `LedgerRuntime` facade (`tests/conformance/test_enforcement_profiles.py`,
`tests/unit/test_core_runtime.py`).

The mypy path written in `IMPLEMENTATION_STATE.md` and repeated in the plan as
`packages ges/mcp/src` is a typo. The path CI actually uses is `packages/mcp/src`; that is the one
recorded above and the one used for every later stage.

## Timings

| Measurement | Wall clock |
|---|---|
| Full `pytest -m "not live_llm"` with coverage | 20.96 s (22.24 s including interpreter start) |
| `tests/contract/test_v1_replay.py` (10 tests, six v1 fixtures) | 0.44 s (1.26 s including start) |

The v1 replay fixtures are small by construction — 22 events across six files — so this timing is a
regression tripwire, not a scaling measurement. The scaling concern that Stage 6 addresses is not
observable at this fixture size, which is precisely why Stage 6 adds an explicit
`replay.max_events_warn` threshold rather than relying on a timing signal.

## Ten largest source files

| Lines | File |
|---|---|
| 3230 | `belief_ledger_pramana/runtime.py` |
| 1464 | `packages/core/src/belief_ledger_core/store.py` |
| 1178 | `packages/core/src/belief_ledger_core/api.py` |
| 1097 | `packages/core/src/belief_ledger_core/enforcement.py` |
| 876 | `belief_ledger_pramana/config.py` |
| 759 | `packages/core/src/belief_ledger_core/migrations.py` |
| 723 | `packages/core/src/belief_ledger_core/projections.py` |
| 615 | `tests/core/test_safety_regressions.py` |
| 548 | `evaluations/report.py` |
| 513 | `packages/core/src/belief_ledger_core/manifest.py` |

Total Python across the repository, excluding `.venv/`, `build/`, `dist/` and `__pycache__/`:
31 923 lines.

## R1 — Replay recomputation

**`R1: replay-independent`.**

Relabel output is materialised into events. `BELIEF_STATUS_CHANGED` and `DEFEAT_ADDED` are written
by `packages/core/src/belief_ledger_core/api.py:545` and `belief_ledger_pramana/runtime.py:1769`,
and replay reapplies them through the handler table in
`packages/core/src/belief_ledger_core/projections.py:334`. Replay never calls `relabel()` or
`compare_priority()`.

Verified by experiment, not by reading alone. A deliberately outcome-changing tweak was applied to
`compare_priority` — the sign of the lexicographic result was inverted — and then reverted:

| Suite | With inverted comparison | Reverted |
|---|---|---|
| `tests/contract/test_v1_replay.py` | 10 passed | 10 passed |
| `tests/unit/test_engine.py` | 1 failed, 4 passed | 5 passed |

The engine suite detects the change and the frozen replay suite does not. Defeat semantics are
therefore free to change without altering frozen v1 event or projection hashes.

**Consequence for Stage 3:** the recency change does not touch frozen replay output. No fixture
copy, no legacy fixture directory, and no ADR 0011 clause about broken legacy replay equivalence is
required. ADR 0011 still records the semantic change itself.

## R2 — Frozen record kinds

Every aggregate type and record kind appearing in `tests/fixtures/v1_replay/*.jsonl`, across all
22 events in the six fixtures:

**Record kinds (11):** `BELIEF_ADMITTED` (3), `BELIEF_STATUS_CHANGED` (1), `DEFEAT_ADDED` (1),
`EPISODE_CREATED` (6), `EPISODE_FINALIZED` (1), `EPISODE_TURN_STARTED` (1), `EVIDENCE_INGESTED` (3),
`GATE_DECIDED` (2), `LINT_RECORDED` (1), `RETRACTION_CREATED` (1), `SOURCE_REGISTERED` (2).

**Aggregate types (8):** `belief` (4), `defeat` (1), `episode` (8), `evidence` (3),
`gate_decision` (2), `lint_report` (1), `retraction` (1), `source` (2).

**Consequence for Stage 4:** neither `ComponentVerdict` nor `LlmUsage` appears in any frozen
fixture, and there is no verification-related record kind among them. A new sibling record kind is
therefore hash-neutral with respect to the frozen fixtures. Stage 4 must still add no required field
to any of the 11 kinds above.

## R3 — Public compat surface

Recorded in full in [compat-surface.md](compat-surface.md). Summary of what is *promised* rather
than merely importable:

- `belief_ledger_pramana.__all__` promises exactly `Pramana`, `Stakes`, `Status`, `__version__`.
- `plugin.yaml` promises the entry point `belief_ledger_pramana.plugin`, four tools, and thirteen
  hooks.
- `scripts/check_product_claims.py` pins the Hermes contract commit in `README.md` and
  `docs/integrations/hermes.md`, not any Python symbol.
- `docs/python-api.md` directs new integrations at `belief_ledger_core`, and states that adapter
  internals are not the supported import path.
- `tests/core/test_public_api.py` exercises `belief_ledger_core` only. It asserts nothing about
  `belief_ledger_pramana`, so before Stage 7a the compat surface has no automated pin at all.

That gap is the reason Stage 7a exists and must land before any move in 7b–7d.

## Answers to the Stage 0 question block

Recorded when answered; see [the plan findings register](plan-findings.md) for anything the answers
displaced.

| Question | Answer |
|---|---|
| Q1 — priority claim reconciliation | _pending_ |
| Q2 — external anchor sink | _pending_ |
| Q3 — `LedgerRuntime` facade | _pending_ |
| Q4 — `runtime.py` split depth | _pending_ |
| Q5 — delivery shape | _pending_ |
