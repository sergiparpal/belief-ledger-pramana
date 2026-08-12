# 0010 — Scalar competence stays in the priority order, and the documentation says so

- **Status:** accepted, 2026-08-10
- **Supersedes nothing.** Constrains [the specification](../belief-ledger-pramana-spec-v0.1.md)
  §1 and §4.2.

## Context

The specification's §1 said: "scalar confidence exists as an auxiliary field but does not govern
defeat." Read on its own, that sentence says no scalar decides defeat. The code has always
disagreed. `priority_trace` puts `reliability_rank`, computed by `effective_competence`, third in
the five-key lexicographic tuple, and §4.2 of the same document lists it there in plain sight.

Both statements were defensible in isolation. `Belief.confidence` genuinely is auxiliary — no code
path in `engine/priority.py` reads it, which is what R1 was asserting. But a reader who takes §1 as
the summary and never reaches §4.2 concludes that defeat is purely structural, and that conclusion
is wrong. Two documents in this repository have already drifted from code constants and been
corrected by hand; this is the same failure in the semantic layer, where it matters more.

Investigating it turned up something neither document stated. `_type_key` bands SHABDA into
`shabda_apta_hi`, `_mid` and `_lo` using the same `effective_competence` scalar. For testimony the
scalar therefore also determines `type_rank`, the *second* key. A competence gap that crosses a band
boundary is decided at `type_rank` and never reaches `reliability_rank` at all. "The scalar is the
third key" is true of the tuple's construction and false as a bound on the scalar's influence.

## Decision

Keep the behaviour. Fix the claim, and fix it precisely enough to survive the band coupling.

1. `reliability_rank` stays the third key of
   `(integrity_rank, type_rank, reliability_rank, specificity_rank, recency_rank)`. No behaviour
   changes under this record.
2. `engine/priority.py` carries a module docstring stating the order, what the scalar is (a
   competence estimate for the source, not a confidence over the belief), what it can decide, what
   it can never override, and the SHABDA band coupling.
3. §1 and §4.2 of the specification are corrected to match, including the band coupling.
4. `tests/unit/test_priority_order.py` pins all of it structurally: the tuple's contents and order,
   the field order on `PriorityTrace`, that reliability decides a tie the first two keys left, that
   it loses to a differing `integrity_rank` and to a differing `type_rank`, that `Belief.confidence`
   is never read, that `positive_over_anupalabdhi` outranks the whole tuple, and that a total tie
   is saṃśaya rather than an arbitrary winner.

## Alternative considered and rejected

Removing `reliability_rank` from the tuple — option B of the plan's Q1 — would make the original
§1 sentence true as written. It was rejected for this pass.

Every contest that the scalar currently settles would become an all-key tie, and an all-key tie is
saṃśaya: both beliefs go to `PENDING` with a conflict and a `VerificationTask`. `PENDING` currently
has no active exit. Verification tasks are created at ingestion and are never pulled at a gate, so
the queue only grows. Trading a documented tiebreak for an unbounded `PENDING` population is a worse
position than the one being corrected, and it would have to come *after* verification is made
pull-at-gate rather than before.

That ordering is the substance of the rejection, not a preference. Revisit this record once
`PENDING` has a drain.

## Consequences

- No runtime behaviour changes. Frozen v1 replay fixtures are untouched, and defeat semantics are
  replay-independent in any case (see `docs/obvious-fix-baseline.md`, R1).
- The documentation is now more precise than it was, and harder to keep precise: the band coupling
  is a genuine complication and a future reader may be tempted to simplify the sentence back.
  `tests/unit/test_priority_order.py::test_for_shabda_a_competence_gap_across_a_band_boundary_is_decided_at_type`
  exists to make that simplification fail.
- The self-confirming loop between `effective_competence` and defeat outcomes — a source loses
  competence when its beliefs are defeated, and lower competence makes future defeat more likely —
  is untouched and remains out of scope. It is the plan's §2.3 and needs defeat-by-different-pramāṇa
  separated from defeat-by-configured-tiebreak before it can be addressed.
