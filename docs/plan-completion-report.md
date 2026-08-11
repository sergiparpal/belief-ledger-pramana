# Obvious-fix plan — completion report

Closes `belief-ledger-pramana-obvious-fixes-plan.md`. Measured against
[the Stage 0 baseline](plan-baseline.md); the register of everything found along the way is
[plan-findings.md](plan-findings.md).

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
- [x] `docs/plan-findings.md` and this report exist and are complete
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

Twenty-six entries, reproduced in full in [plan-findings.md](plan-findings.md). The ones worth
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
