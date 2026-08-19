# 0016 — The recency key is full-precision, not truncated to seconds

- **Status:** accepted, 2026-08-19
- **Constrains** [the specification](../belief-ledger-pramana-spec-v0.1.md) §4.2.
- **Refines** [ADR 0011](0011-unconditional-recency-key.md), which made the same key unconditional.
- **Closes** [#31](https://github.com/sergiparpal/belief-ledger-pramana/issues/31).

## Context

`priority_trace` computed the fifth key as `int(observed_at.timestamp())`. `int()` truncates a float
epoch to whole seconds, which aligns `recency_rank` to a fixed one-second grid. Whether two beliefs
tie on recency therefore depended not on how far apart they were but on where a second boundary
happened to fall:

| pair | gap | `recency_rank` |
|---|---|---|
| `12:00:00.999` / `12:00:01.001` | 2 ms | differs — the fresher one wins |
| `12:00:00.001` / `12:00:00.999` | 998 ms | ties — saṃśaya, both `PENDING` |

Two claims 2 ms apart resolved decisively; two claims 500× further apart went to `PENDING`. That is
not a contemporaneity window — a window is `abs(a - b) < tolerance`, which is a property of the pair.
It is an artifact of grid alignment, which is a property of the calendar.

ADR 0011 made recency unconditional precisely because `PENDING` has no active exit: "every such pair
adds permanently to a queue that nothing drains, and the claim stops supporting actions in the
meantime." Truncation reintroduced that harm non-deterministically for any pair observed inside one
second. Conversational turns are seconds or minutes apart and were unaffected; batch and tool
ingestion of several observations within one second was not, and which pairs landed in saṃśaya was
decided by clock alignment rather than by anything about the beliefs.

The behaviour surfaced as a flaky `hermes-adapter (3.12)` job on #29: the same commit produced
`pending`/`pending` on one run and `out`/`in` on another. #30 pinned the ingestion clock in
`tests/integration/test_semantic_contradiction.py` and left the engine alone, recording this as the
open question. The build was the symptom; this record is about the engine.

## Decision

Compute `recency_rank` as whole microseconds since the epoch, using integer arithmetic on the
`timedelta` rather than a float:

```python
delta = value - _EPOCH
return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
```

`datetime` resolves to one microsecond, so the conversion is lossless. Two beliefs share a
`recency_rank` exactly when they name the same instant, which is what §4.2's "Equality → saṃśaya"
means once no granularity is imposed on top of it.

Position is unchanged. Recency stays fifth and last, so ADR 0011's containment argument carries over
untouched: no belief that would win on integrity, type, reliability or specificity can now lose on
age, and `positive_over_anupalabdhi` is still checked before the tuple is consulted at all.

### Why not `int(observed_at.timestamp() * 1_000_000)`

That is the obvious spelling and it reproduces the defect at a finer grid, with a delayed onset. A
float64 holding seconds since 1970 spaces its representable values ~0.24 µs apart today and further
apart every year, so distinct microseconds collapse back into ties at a rate that grows with the
date. Measured over 100 000 adjacent-microsecond pairs per epoch:

| epoch | pairs that collapse into a tie |
|---|---|
| 2000, 2026, 2100 | 0% |
| 2038 | 9.7% |
| 2260 | 50% |

Across 100 000 random timestamps between 1971 and 2260, `int(ts * 1_000_000)` also differs from the
true microsecond count by 1 µs in roughly 10% of cases. A tie that arrives with a calendar date is
worse than the one this record removes, because no test written today would see it.
`test_distinct_instants_never_share_a_rank_at_any_epoch` is parametrized over exactly these epochs
and fails under the float route at 2038 and 2260.

### Why exact equality rather than a configured tolerance

§4.2 says "Equality **or configured incomparability** → saṃśaya", so a tolerance window would be
spec-compatible rather than a deviation. Nothing implements configured incomparability today — the
phrase appears in the specification and nowhere in the code — and adding it here would re-open the
harm ADR 0011 was written to close, with an operator-chosen width in place of an accidental one. Any
pair inside the window returns to a `PENDING` queue that nothing drains.

Exact equality is therefore what ships. A tolerance remains available as a later, separately
motivated decision; it needs a configuration key, a documented default, and its own argument for
that default's value, none of which this record supplies.

## Consequence for clock skew

ADR 0011 recorded that unconditional recency gives clock skew a path to influence defeat at the last
key. This change does not narrow that path, and the honest statement is that it widens it in one
direction: under truncation a source needed to shift a timestamp by up to a full second to flip an
outcome, and now 1 µs always suffices. What it buys is that the required shift is no longer
*unpredictable* — previously anywhere between 1 µs and 1 s depending on alignment.

The containment is the same positional argument as ADR 0011: a skewed timestamp cannot overcome any
difference in integrity, type, reliability or specificity. If skew becomes a real threat rather than
an acknowledged one, the mitigation is the tolerance window above, not a coarser key — a coarser key
does not bound skew, it randomizes which skew succeeds.

## Consequence for context selection

`context/select.py` sorts the bounded context window on `(mandatory, score, *priority_value,
belief.id)`, so the same key orders selection as well as defeat. Beliefs observed inside one second
previously fell through to `belief.id` as the final tiebreak, meaning which of them entered a full
`max_beliefs` window was decided by identifier rather than by age; they are now ordered by
observation time. This is the same correction in a second place, and it is recorded here because
ADR 0011's "bounded by position in the tuple" argument was written about defeat: in selection the
tuple is spliced into a longer key with `scores` above it and `belief.id` below.

## Consequence for the frozen v1 fixtures

None, for the reason ADR 0011 gives: defeat semantics are replay-independent, because `relabel`
output is materialised into `BELIEF_STATUS_CHANGED` and `DEFEAT_ADDED` events and replay reapplies
those events rather than re-running the engine. `tests/contract/test_v1_replay.py` stays green and
`tests/fixtures/v1_replay/` is untouched.

## Consequence for what is rendered

`recency_rank` is not persisted. It is computed on demand and rendered by
`application/queries.py:explain`, reaching users through `hermes/tools.py` and
`hermes/slash_commands.py`. Its magnitude changes from ~1.79e9 to ~1.79e15. No schema, fixture or
contract test pins the value, so nothing breaks; the number is a rank, comparable within one trace
comparison and not across versions, and it was never documented as anything else.

## Consequences

- Two beliefs observed less than a second apart now resolve to one `IN` and one `OUT` instead of
  resolving or not depending on the calendar. Saṃśaya is not abolished: identical timestamps still
  tie on all five keys, and `test_the_same_pair_at_one_timestamp_is_still_pending` remains the
  control.
- One assertion changed. `test_recency_is_computed_for_every_perishability_class` pinned
  `int(FRESHER.timestamp())`, which is the truncation itself rather than a behaviour; it now pins
  the microsecond count, derived in the test independently of the engine's arithmetic. Running the
  full suite against the change produced exactly these four parametrizations as failures and
  nothing else — 579 other tests, the v1 replay contract, and offline evaluation suites A–E were
  unaffected, the evaluation report being identical except for timestamps and timing measurements.
- `_timestamp` is replaced by `_recency_micros`, which carries the same timezone guard with the same
  message. ADR 0011's note on that guard being deliberate redundancy rather than dead code still
  applies, and `test_the_engines_own_timezone_guard_is_kept_as_defence_in_depth` still pins it.
- `tests/integration/test_semantic_contradiction.py` keeps its pinned clock and both regimes. Its
  explanation of *why* the wall clock decided the outcome is now history rather than current
  behaviour, and says so.
