# 0015 — Runtime module layout, and where the split stops

- **Status:** accepted, 2026-08-10
- **Constrains** [ADR 0007](0007-host-neutral-product-surface.md), whose compatibility contract
  this change had to hold fixed.

## Context

`belief_ledger_pramana/runtime.py` was 3,233 lines. Q4 of the obvious-fix plan selected a 600-line
limit, and the plan's method for reaching it is deliberately narrow: pure moves only, one cluster
per commit, and a hard rule that any move requiring a logic change is reverted and recorded rather
than fixed inline. A refactor that also changes behaviour is unreviewable.

Nothing pinned the import surface before this stage. `tests/core/test_public_api.py` exercises
`belief_ledger_core` alone, so 77 reachable modules of a 1.x compatibility contract had no
automated protection at all (F-03). Moving anything before fixing that would have been changing an
unmeasured thing.

## Decision

### Pin first (7a)

`tests/fixtures/compat_surface.json` records every module reachable from `belief_ledger_pramana`
and every name each one exports. `tests/unit/test_compat_surface.py` asserts three separate things:
no module disappears, no exported name disappears, and the snapshot matches exactly. The first two
catch breakage; the third forces an addition to be recorded deliberately rather than absorbed.

It lives in `tests/unit/` rather than `tests/core/`, which the plan suggested. The
`core-no-adapters` CI job runs `tests/core` against a venv holding only `packages/core`, where
`belief_ledger_pramana` is not installed — putting the pin there would break the isolation that job
exists to prove.

### One home for packaged policy data (7b)

`belief_ledger_pramana/data/{defaults,action-policies,source-profiles}.yaml` were byte-identical
copies of core's, guarded by a test asserting they stayed identical. A byte-identity test detects
drift; it cannot prevent it, and it only holds while someone remembers both files exist.

The adapter depends on `belief-ledger-core`, so core's copy is always installed alongside it. The
adapter now loads from `belief_ledger_core.data`, the duplicates are deleted, and the test asserts
there is exactly one copy. The wheel-content contract in `scripts/inspect_artifacts.py` follows.

Re-export shim modules were **not** deleted. The plan permits deleting modules outside the promised
surface, but only four names are formally promised, so that rule would authorize deleting nearly
the whole package. Existing Hermes installations import these paths; removing them is a breaking
change to a compatibility contract with no measured need behind it.

### The facade stays, and says when it goes (7c)

Q3 selected option A. `LedgerRuntime` keeps its `DeprecationWarning`, now pinned by a test, and
`docs/python-api.md` records removal in **2.0.0**.

Its callers were not migrated, and that is not an omission. `LedgerRuntime` is not a thin wrapper
over `BeliefLedger`: `ingest_health` and `authorize_deployment` encode the deployment-gate
fixture's own policy and have no equivalent in the core API. Migrating the callers would mean
rewriting the example, which is design work and out of scope. A test now asserts that asymmetry
rather than leaving it as prose. Recorded as F-22.

### The split, and where it stops (7d)

`runtime.py` became `runtime/`:

| Module | Lines | Contents |
|---|---:|---|
| `__init__.py` | 42 | Re-exports every previously importable name, private helpers included |
| `errors.py` | 36 | `RuntimeUnavailable`, `EpisodeResolutionError`, `ToolEvidence` |
| `helpers.py` | 271 | Module-level pure functions |
| `plugin_runtime.py` | 598 | `PluginRuntime`: host lifecycle, config reload, episode resolution |
| `episode_service.py` | 2 430 | `EpisodeService` |

Every move is a move. The only edits are import rebasing — single-dot relative imports became
double-dot one level deeper — plus the new intra-package imports and a `TYPE_CHECKING` import for
the one annotation that would otherwise be circular. The dependency runs one way,
`plugin_runtime → episode_service`, so there is no runtime cycle.

`__init__.py` re-exports the underscore-prefixed helpers as well. Several tests import them from
`belief_ledger_pramana.runtime` directly; leaving them out would have made this a surface change
wearing a refactor's clothes. They stay out of `__all__`, so nothing is newly *promised*.

**The 600-line target is not met, and cannot be met by pure moves.** `EpisodeService` is one class
of 2,430 lines. Splitting a class across modules requires mixins or method relocation, and both
change the class rather than move it. The plan's own hard rule says to leave such a cluster in
place and record it, which is what happened (F-23).

## The exemption list is a ceiling

`tests/unit/test_architecture.py` fails when any file under `belief_ledger_pramana/` or
`packages/*/src/` exceeds 600 lines, with eight named exemptions. Each records its current size as
a ceiling and a reason, and three further tests keep the list honest: an exempt file may not grow
past its ceiling, a file that falls under the limit must leave the list, and every exemption must
state a real reason.

That structure matters more than the list's current contents. A bare exemption list rots into
permission to grow. A ceiling can only move down.

## Consequences

- The largest source file went from 3,233 lines to 2,430, and the codebase gained a real seam
  between host lifecycle and per-episode work.
- New files over 600 lines are now impossible without an explicit, reasoned entry.
- Test count and coverage both rose across the split (573 → 577 tests, 88.31% → 88.43%), and the
  extra tests are the compat-surface pin parametrizing over the four new modules.
- The remaining decomposition of `EpisodeService` is real work that needs design, not a bigger
  refactor budget. F-23 sketches the seams a future pass would follow.
