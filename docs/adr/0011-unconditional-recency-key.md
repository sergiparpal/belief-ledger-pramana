# 0011 — Recency is a priority key for every perishability class

- **Status:** accepted, 2026-08-10
- **Constrains** [the specification](../belief-ledger-pramana-spec-v0.1.md) §4.2.
- **Related:** [ADR 0010](0010-scalar-competence-in-the-priority-order.md) fixes the description of
  the same tuple.

## Context

`priority_trace` computed `recency_rank` only when `belief.perishability` was `fast` or `live`, and
left it at `0` otherwise. For two `slow` or `stable` beliefs the fifth key was therefore always
`0 == 0`.

The consequence appears when everything else ties. Two contradictory claims from the same source,
of the same pramāṇa, at the same specificity, differing only in when they were observed, tie on all
five keys. An all-key tie is saṃśaya, so `relabel` marks both `PENDING`, records a
`samsaya:<id>` cause, opens a conflict and emits a `VerificationTask`.

That is the right answer when the ledger genuinely has no reason to prefer one claim. It is the
wrong answer here, because the ledger does have a reason: one observation is more recent than the
other. And the cost is not neutral. `PENDING` has no active exit — verification tasks are created
at ingestion and never pulled at a gate — so every such pair adds permanently to a queue that
nothing drains, and the claim stops supporting actions in the meantime.

Perishability answers "how quickly does this go stale", which is a real and separate question. It
was being made to answer "is age comparable at all", which it should not.

## Decision

Compute `recency_rank` from `observed_at` for every perishability class.

It stays the **fifth and last** key. That is the whole containment argument: recency can only settle
a contest that `integrity_rank`, `type_rank`, `reliability_rank` and `specificity_rank` all left
tied, so no belief that would previously have won on any structural ground can now lose on age. The
blast radius is bounded by position in the tuple, not by a guard that has to be maintained.

`positive_over_anupalabdhi` is checked before the tuple is consulted at all and is unaffected: a
fresher admitted absence still loses to older positive evidence.

## Consequence for the frozen v1 fixtures

None. Defeat semantics are replay-independent: `relabel` output is materialised into
`BELIEF_STATUS_CHANGED` and `DEFEAT_ADDED` events, and replay reapplies those events through the
projection handler table rather than re-running the engine. This was verified by experiment in
`docs/plan-baseline.md` (R1) — inverting the sign of the lexicographic comparison fails
`tests/unit/test_engine.py` and leaves `tests/contract/test_v1_replay.py` green.

`tests/fixtures/v1_replay/` is therefore untouched, no legacy fixture directory is needed, and
replay equivalence is not broken at this version.

## Timezone awareness moves to the model boundary

`_timestamp` raises on a naive `datetime`. That raise was previously reachable only for `fast` and
`live` beliefs; making recency unconditional makes it reachable for all of them, so a naive value
anywhere would now surface deep inside defeat resolution.

`Belief.__post_init__` now rejects a naive `observed_at` instead, which is where the repository
already enforces this rule: `parse_datetime` refuses naive serialized values and `FixedClock`
refuses a naive fixed time, both with the same message. The check in `_timestamp` is deliberately
kept as redundancy for a future caller that builds a trace from something other than a `Belief`;
`test_the_engines_own_timezone_guard_is_kept_as_defence_in_depth` pins it so it is not mistaken for
dead code.

One existing test changed as a result. `tests/unit/test_domain_edges.py` constructed a naive
`Belief` and asserted that `priority_trace` rejected it. It now asserts that the construction
itself is rejected, and additionally that an aware belief produces a non-zero `recency_rank`. This
asserts strictly more than before: the invalid value can no longer be built, let alone reach the
engine. Recorded in [the findings register](../plan-findings.md) as F-12.

## Consequences

- Stale-versus-fresh pairs resolve to one `IN` and one `OUT` instead of two `PENDING`. Every
  existing test and evaluation suite produced the same outcome before and after, so the change is
  inert on the current fixtures and is exercised by the new tests in
  `tests/unit/test_recency_priority.py`. That the suites do not cover this case is itself worth
  knowing, and is recorded as F-11.
- Saṃśaya is not abolished. Two beliefs at the same timestamp still tie on all five keys and still
  go to `PENDING`; `test_the_same_pair_at_one_timestamp_is_still_pending` is the control.
- Clock skew between sources now has a path to influence defeat, at the last key only, in contests
  that were previously undecidable. That is a real widening of what a source can affect by
  misreporting time, and it is bounded by the same position argument: a skewed timestamp cannot
  overcome any difference in integrity, type, reliability or specificity.
