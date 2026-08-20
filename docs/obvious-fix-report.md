# Obvious-fix plan — completion report

Closes the obvious-fix plan, an external working document that is not kept in this
repository. Measured against
[the Stage 0 baseline](#appendix--stage-0-baseline) in the appendix; the register of everything
found along the way is
[obvious-fix-findings.md](obvious-fix-findings.md).

## Baseline versus final

| Measure | Stage 0 baseline | Final | Δ |
|---|---|---|---|
| Commit | `3d21ccddce66a9659f3509cc1b5758a025541c52` | `plan/stage-8-reporting` | 12 commits |
| Tests | 353 passed, 0 failed, 0 skipped | 582 passed, 0 failed, 0 skipped | **+229** |
| Combined coverage | 88.16% | 88.46% | **+0.30 pp** |
| Statements / branches | 9 495 / 2 818 | 10 105 / 2 964 | +610 / +146 |
| Coverage floor | 88 | 88 | unchanged |
| `ruff format --check` / `ruff check` | 0 / 0 | 0 / 0 | — |
| `mypy` (source files) | 0 (146 files) | 0 (158 files) | — |
| Largest source file | 3 233 (`runtime.py`) | 2 430 (`runtime/episode_service.py`) | **−803** |
| Full `pytest` wall clock | 20.96 s | 29.87 s | +8.9 s for +229 tests |
| v1 replay wall clock | 0.44 s | 0.33 s | — |
| ADRs | 9 | 15 | +6 |
| Findings recorded | — | 26 | — |
| Warnings | 8 (intentional `LedgerRuntime`) | 8 (same) | unchanged |

`tests/fixtures/v1_replay/` is **byte-for-byte unchanged**. `git diff main -- tests/fixtures/v1_replay/`
is empty, and the frozen projection hashes still verify — across a schema bump from 7 to 8, a new
record kind, a defeat-semantics change and a 3,233-line module split.

### Ten largest source files

| Lines | File | Baseline |
|---:|---|---:|
| 2430 | `belief_ledger_pramana/runtime/episode_service.py` | (part of the 3 233-line `runtime.py`) |
| 1601 | `packages/core/src/belief_ledger_core/store.py` | 1 464 |
| 1178 | `packages/core/src/belief_ledger_core/api.py` | 1 178 |
| 1097 | `packages/core/src/belief_ledger_core/enforcement.py` | 1 097 |
| 900 | `belief_ledger_pramana/config.py` | 876 |
| 778 | `packages/core/src/belief_ledger_core/migrations.py` | 759 |
| 723 | `packages/core/src/belief_ledger_core/projections.py` | 723 |
| 738 | `belief_ledger_pramana/hermes/cli.py` | 508 |
| 615 | `tests/core/test_safety_regressions.py` | 615 |
| 597 | `belief_ledger_pramana/runtime/plugin_runtime.py` | — |

`store.py`, `config.py`, `migrations.py` and `hermes/cli.py` grew because Stages 4–6 added
features to them. Every one is inside the ceiling recorded in `OVERSIZED_EXEMPTIONS`, and the guard
now prevents any of them growing further — as it demonstrated during the audit below, when adding
the `doctor` check pushed `hermes/cli.py` past its 723-line ceiling and failed the build until the
move was stated (F-26).

## Stage by stage

| Stage | What changed | ADR | Branch |
|---|---|---|---|
| 0 | Baseline, `R1`/`R2`/`R3` resolved, findings register opened, question block answered (all defaults) | — | `plan/stage-0-baseline` |
| 1 | `scripts/check_doc_invariants.py`: 6 guarded facts across 9 files plus migration coverage; wired into `verify_stage.py` and CI | — | `plan/stage-1-doc-invariants` |
| 2 | Specification corrected on how a scalar participates in defeat; tuple order pinned structurally | 0010 | `plan/stage-2-priority-claim` |
| 2b | Self-claim privilege bound to the user channel structurally; pattern limits characterised | — | `plan/stage-2b-self-claim-scope` |
| 3 | `recency_rank` computed for every perishability class; timezone guarantee moved to the model boundary | 0011 | `plan/stage-3-recency` |
| 4 | `LLM_CALL_ATTRIBUTION` record, `SamplingPolicy`, `llm-divergence` command | 0012 | `plan/stage-4-llm-attribution` |
| 5 | `ChainAnchorPort`, `FileAnchorSink`, `anchor publish`/`verify`, `db verify-chain --against-anchors` | 0013 | `plan/stage-5-chain-anchoring` |
| 6 | Schema 8 `snapshots` cache, snapshot commands, `verify-snapshot`, `replay.max_events_warn` | 0014 | `plan/stage-6-snapshots` |
| 7 | Compat surface pinned, packaged data deduplicated, `runtime.py` split, size guard with ceilings | 0015 | `plan/stage-7-consolidation` |
| 8 | Final gate and this report | — | `plan/stage-8-reporting` |

No pull requests were opened. Q5 was answered "local branches only": `CLAUDE.md` rule 9 forbids
pushing without authorization and none was given, so each stage is a local branch stacked on the
previous one, ending at `plan/stage-8-reporting`. Merging them in order reproduces the whole series.

## In-scope items not completed

One, and it is partial.

**Stage 7d did not reach the 600-line limit.** `runtime.py` went from 3,233 lines to a package
whose largest module is 2,430, but `EpisodeService` is a single class and splitting a class across
modules is not a pure move — it needs mixins or method relocation, both of which change the class
rather than move it. The plan's own hard rule for 7d is to leave such a cluster in place and record
it, which is what happened (F-23, with the seams a design pass would follow). The same blocker
applies to `store.py`, `api.py` and `enforcement.py`.

The size guard shipped regardless, with eight exemptions that each carry a ceiling and a reason,
plus tests that stop an exempt file growing and force a file that drops under the limit to leave
the list. New files over 600 lines are now impossible without an explicit, reasoned entry.

Two related decisions inside Stage 7 departed from the plan's letter and are recorded rather than
buried: re-export shims were kept rather than deleted (F-24), and the `LedgerRuntime` callers were
not migrated (F-22). Both are explained in [ADR 0015](adr/0015-runtime-module-layout.md).

## Nothing from §0.2 was implemented

Verified item by item. Evaluation methodology (§1.1–§1.6) is untouched; no threshold in
`evaluations/config.yaml` was adjusted (§1.3); no modality axis was added to testimony (§2.1);
`effective_competence`'s feedback loop is unchanged (§2.3) though F-07 records that it is worse
than documented; `integrity_rank` still dominates lexicographically (§3.1); verification is still
push-at-ingest (§4.1) — which is precisely why ADR 0010 rejected removing `reliability_rank`;
typed reasoning is still episode-scoped (§5.1); the Hermes profile cap is unchanged (§6.2); and
§7.1/§7.2 needed no code.

## Definition of done

- [x] Every in-scope stage is complete, or listed above with its reason
- [x] `scripts/verify_stage.py all` exits 0
- [x] Coverage 88.46% ≥ the 88.16% baseline
- [x] `scripts/check_doc_invariants.py` exists, is in CI, and has passing negative tests
- [x] `tests/fixtures/v1_replay/` unchanged
- [x] Every semantic change has an ADR, indexed in `docs/adr/README.md` and traced in
      `docs/requirements-traceability.md`
- [x] `docs/obvious-fix-findings.md` and this report exist and are complete
- [x] No item from §0.2 implemented

## Post-implementation audit

The plan was re-read against the tree after Stage 8, deliverable by deliverable rather than stage
by stage. Two items had been delivered only in part and were closed in the audit:

- §9.2 requires the replay budget warning "through `doctor` and the replay command itself". Only
  `db replay` had it; `doctor` now carries a `replay_budget` check that warns at the same
  threshold without changing its health verdict.
- §6.5 requires the recency change to update the specification's §3 as well as §4.2. §3's
  PRATYAKSHA row still described re-observation as rebutting "for FAST/LIVE facts", which ADR 0011
  makes an understatement. It now states the new scope.

Both are recorded as F-25. The pattern is worth naming: in each case the headline of a stage was
delivered and a secondary clause of the same paragraph was not, which is the kind of gap a
stage-level "done" judgement does not catch.

Everything else verified clean: all nine plan-specified CLI commands parse with their documented
flags, every test the plan names by behaviour exists, no CI step was removed (59 → 61 steps), the
coverage floor is untouched at 88, all six new ADRs are indexed and traced, and
`tests/fixtures/v1_replay/` still diffs empty against `main`.

## Findings register

Twenty-six entries, reproduced in full in [obvious-fix-findings.md](obvious-fix-findings.md). The ones worth
reading first:

| ID | Severity | Why it matters |
|---|---|---|
| F-07 | significant | For SHABDA, source competence also sets `type_rank`, so the scalar reaches further into defeat than any document said |
| F-08 | significant | The self-claim privilege waives cross-source verification at HIGH stakes; it is not the competence bump it was described as |
| F-10 | significant | The self-claim pattern has no negation handling and covers two languages |
| F-11 | significant | No test or evaluation suite covered stale-versus-fresh defeat — a decision the engine makes on every relabel |
| F-16 | significant | Sampling was a hardcoded literal in two places; correct, but indistinguishable from uncontrolled |
| F-20 | significant | A migration test had silently stopped exercising its migration |
| F-23 | significant | `EpisodeService` cannot be split by a pure move |
| F-03 | significant | The compatibility surface had no automated pin at all — now closed |

Four entries record an existing test that had to change (F-12, F-15, F-19, F-20). In each case the
replacement asserts strictly more than the original, and none weakened a safety assertion.

The register earned its place. Half of these were found by writing a test that failed on its first
run — F-07 by the priority pin, F-11 by the recency suite, F-20 by the schema bump, F-21 by the
snapshot corruption case — which is the argument for writing the test before believing the
description.

## Appendix — Stage 0 baseline

The measured state of the repository before any stage of the plan was implemented. Every stage
reported its numbers against these, and a stage that reduced one without an explanation in
[the findings register](obvious-fix-findings.md) was a failed stage. Kept because the report above
compares against it and because ADRs 0010, 0011 and 0012 cite the R1 experiment as evidence.

### Commit

| Item | Value |
|---|---|
| Baseline commit | `3d21ccddce66a9659f3509cc1b5758a025541c52` |
| Branch | `plan/stage-0-baseline` off `main` |
| Date measured | 2026-08-10 |
| Python | 3.13 (workspace `.venv`) |
| Hermes host | `hermes-agent==0.19.0` installed as a peer |

### Gate results

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
| Total | 88.16% (88.2% at the configured precision of 1) |

**88.16% is the number later stages must not fall below.** It was measured from a clean tree with
untracked files stashed. A measurement taken with the plan's own new test files present but the
sources they test stashed reports 88.18%, which is an artefact of the partial tree and not a real
baseline; the figure is recorded here so no later stage mistakes that artefact for a regression.

The eight warnings are all intentional `DeprecationWarning`s from tests that deliberately exercise
the `LedgerRuntime` facade (`tests/conformance/test_enforcement_profiles.py`,
`tests/unit/test_core_runtime.py`).

The mypy path written in `IMPLEMENTATION_STATE.md` and repeated in the plan as
`packages ges/mcp/src` is a typo. The path CI actually uses is `packages/mcp/src`; that is the one
recorded above and the one used for every later stage.

### Timings

| Measurement | Wall clock |
|---|---|
| Full `pytest -m "not live_llm"` with coverage | 20.96 s (22.24 s including interpreter start) |
| `tests/contract/test_v1_replay.py` (10 tests, six v1 fixtures) | 0.44 s (1.26 s including start) |

The v1 replay fixtures are small by construction — 22 events across six files — so this timing is a
regression tripwire, not a scaling measurement. The scaling concern that Stage 6 addresses is not
observable at this fixture size, which is precisely why Stage 6 adds an explicit
`replay.max_events_warn` threshold rather than relying on a timing signal.

### Ten largest source files at baseline

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

### R1 — Replay recomputation

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

### R2 — Frozen record kinds

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

### R3 — Public compat surface

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

### Answers to the Stage 0 question block

Recorded when answered; see [the plan findings register](obvious-fix-findings.md) for anything the answers
displaced.

Answered 2026-08-10. Every answer is the plan's default option A.

| Question | Answer | What it selects |
|---|---|---|
| Q1 — priority claim reconciliation | **A** | Amend the documentation; no behaviour change; pin the field order with a structural test |
| Q2 — external anchor sink | **A** | Local append-only JSONL file outside the ledger directory; no HTTP adapter |
| Q3 — `LedgerRuntime` facade | **A** | Migrate internal callers; keep the facade emitting `DeprecationWarning`; document a removal version |
| Q4 — `runtime.py` split depth | **A** | Split until no source file exceeds 600 lines |
| Q5 — delivery shape | **A**, local only | One branch per stage, full offline gate before each; nothing pushed and no pull request opened |

Q5 was extended beyond the plan's wording because `CLAUDE.md` rule 9 forbids pushing without
authorization while the plan assumes pull requests through `ci-complete`. Authorization was not
given, so every stage lands as a local branch for review. The branches form a stack — each stage
branches from the previous stage's tip, because Stage 6 depends on Stage 1 and Stage 7 depends on
all of them — and are listed under [Stage by stage](#stage-by-stage) above.
