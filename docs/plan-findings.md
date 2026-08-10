# Obvious-fix plan — findings register

Append-only. Entries are never edited or removed once written; a superseded entry gets a later
entry that supersedes it, and the original stays. Opened at Stage 0 of
`belief-ledger-pramana-obvious-fixes-plan.md` and carried to
[the completion report](plan-completion-report.md).

An entry belongs here whenever any of the following happens: a bug is found outside the plan's
scope, an existing test has to change, a coupling blocks a pure move, a limitation is deliberately
left in place, or the work starts to look like design rather than implementation.

---

### F-01 — Documented schema version is prose, not a checkable constant

- **Stage:** 0
- **Severity:** minor
- **What:** `LATEST_SCHEMA_VERSION = 7` lives at
  `packages/core/src/belief_ledger_core/migrations.py:438`. Three documents state it in three
  different renderings: `docs/upgrade-and-rollback.md:34` says "The current schema is 7",
  `docs/architecture.md:47` says "Schema v7", `docs/operations.md:48` says "Schema v7". None uses a
  form a naive string check would find from the constant name.
- **Why not fixed here:** it is not a defect — it is the constraint Stage 1 has to design around.
  Recorded so the Stage 1 checker's per-fact rendering set is traceable to an observation rather
  than to an assumption.
- **Suggested next step:** Stage 1 gives each fact a set of accepted renderings and requires at
  least one match per listed document.

### F-02 — Three schema versions have no migration SQL file

- **Stage:** 0
- **Severity:** minor
- **What:** `belief_ledger_pramana/data/migrations/` and
  `packages/core/src/belief_ledger_core/data/migrations/` each contain `0001_initial.sql`,
  `0002_llm_reservations.sql`, `0003_performance_indexes.sql` and `0006_enforcement.sql`. Versions
  4, 5 and 7 exist only as the in-code constants `SCHEMA_V4`, `SCHEMA_V5` and `SCHEMA_V7` in
  `packages/core/src/belief_ledger_core/migrations.py`. Nothing currently asserts that every version
  in `1..LATEST_SCHEMA_VERSION` is covered by one or the other, so a genuinely missing version would
  look identical to this deliberate split.
- **Why not fixed here:** the split is intentional and correct — those three versions have no DDL —
  so the fix is a check, not a file. It is the sixth fact in Stage 1's table.
- **Suggested next step:** Stage 1 asserts coverage of every version by SQL file *or* code
  constant, which distinguishes the deliberate case from the accidental one.

### F-03 — `belief_ledger_pramana` had no automated import-surface pin before Stage 7a

- **Stage:** 0
- **Severity:** significant
- **What:** `tests/core/test_public_api.py` imports only from `belief_ledger_core`. No test in the
  repository asserts that any symbol remains importable from `belief_ledger_pramana`, even though
  ADR 0007 makes that package a 1.x compatibility contract and `plugin.yaml` names it as the
  Hermes entry point. Seventy-seven modules are reachable; four names are promised.
- **Why not fixed here:** it is Stage 7a's entire deliverable, and Stage 7a is ordered last by
  design.
- **Suggested next step:** land Stage 7a's snapshot test before any move in 7b–7d, exactly as the
  plan sequences it.

### F-04 — `_defeat_cycle_nodes` is a private name inside a public `__all__`

- **Stage:** 0
- **Severity:** minor
- **What:** `belief_ledger_pramana/engine/defeat.py` re-exports `_defeat_cycle_nodes` through its
  `__all__`, so an underscore-prefixed name is advertised as part of the module's intended surface.
- **Why not fixed here:** removing it from `__all__` is a surface change, and the surface has no
  pin yet (F-03). Doing it before Stage 7a would be changing an unmeasured thing.
- **Suggested next step:** Stage 7a records it in the snapshot as present-but-private; Stage 7b
  decides whether to drop it from `__all__` under the deprecation path.

### F-05 — Two of the three documents named for the schema fact never stated it at all

- **Stage:** 1
- **Severity:** minor
- **What:** the plan's Stage 1 table requires `LATEST_SCHEMA_VERSION` to appear in
  `docs/upgrade-and-rollback.md`, `docs/operations.md` and `docs/architecture.md`. On first run the
  new checker reported that only the first stated it; the other two discussed schema v6 and v7 by
  name but never said which version is current. `README.md` likewise never stated the
  `requires-python` range that the plan requires of it — only `HERMES_COMPATIBILITY.md` did.
- **Why not fixed here:** it *was* fixed here. The entry exists because the failure mode is worth
  recording: this is absence, not staleness, and a checker that only compared stated values would
  have passed all three files silently. That is why a listed document matching the pattern nowhere
  is a failure rather than a skip.
- **Suggested next step:** none; keep the "states it nowhere" branch of the checker, and keep
  `tests/unit/test_doc_invariants.py::test_a_document_that_drops_the_fact_entirely_fails` pinning
  it.
