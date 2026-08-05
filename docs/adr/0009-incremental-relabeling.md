# ADR 0009: Incremental relabeling is rejected; contradiction detection is incrementalized instead

Status: proposed, 2026-08-05.

## Context

The 2026-08-05 codebase review recorded the dominant per-ingestion cost as "the O(N²) relabel
path" and deferred it to an ADR. Measuring it first shows the attribution was wrong, and the
correction changes what should be built.

`EpisodeService._after_new_beliefs` runs three whole-episode passes on every ingestion:
`_detect_deterministic_rebuts`, `relabel`, and `_complete_passive_tasks` (with a second `relabel`
when passive verification completes a task). Timing each phase separately while growing one episode
gives per-call means in milliseconds:

| beliefs | one ingestion | `relabel` | `_detect_deterministic_rebuts` | `_complete_passive_tasks` |
|--------:|--------------:|----------:|-------------------------------:|--------------------------:|
|      64 |          14.1 |      3.58 |                           2.05 |                      0.29 |
|     129 |          18.7 |      3.82 |                           3.39 |                      0.28 |
|     258 |          33.7 |      4.63 |                           7.97 |                      0.28 |
|     387 |          63.0 |      5.48 |                          15.46 |                      0.28 |
|     516 |          95.8 |      6.57 |                          26.27 |                      0.28 |

Across 560 ingestions the cumulative split is `_detect_deterministic_rebuts` 17.5 s (66%),
`relabel` 3.9 s (15%), `_complete_passive_tasks` 0.16 s (under 1%).

Relabeling is not the problem. It grows roughly linearly — 1.8× for 8× the beliefs — and issues a
constant 11 SQL statements per call regardless of episode size, so it is neither N+1 nor quadratic.
Contradiction detection is the quadratic term: 12.8× for the same 8×. Its `token_index` accumulates
a posting list per token, shared vocabulary makes those lists grow with the episode, and every
belief walks the posting list of every token it contains. Worse, the whole pairwise scan repeats
from scratch on each ingestion — `considered` is a local set, discarded when the call returns — so
the cost summed over an episode is cubic in its length, which is what the 66% share reflects.

Nothing bounds an episode's belief count. `context.max_beliefs` (50) is a rendering budget, and
`relabel` deliberately passes no limit to `list_beliefs` so that correctness-sensitive reads cannot
silently lose recent beliefs; a test pins that. Episode length is therefore bounded only by host
behaviour.

## Decision

**The relabel fixed point stays whole-episode.** Its measured share does not justify the risk, and
the risk is real: the algorithm in `engine/defeat.py` computes properties that are not local to any
event-derived dirty set.

- Reinstatement is global. A belief returns to `IN` when its winning attacker falls, so the
  candidate set cannot be derived from the beliefs an event touched.
- Oscillation detection needs the whole defeat graph. `_defeat_cycle_nodes` runs Kosaraju over
  rebut edges and undercut-to-owner edges to assign `samsaya:defeat_cycle`, and a strongly connected
  component is not discoverable from a partial graph.
- The iteration ceiling assigns `PENDING` to an entire component, which again presumes the component
  is known.
- Staleness is time-driven, not event-driven. `_is_stale` compares `observed_at` against
  `perishability_ttl`, so a belief can require a transition with no event having occurred. No dirty
  set computed from appended events is sound.
- Equal-priority contradictions are recomputed each pass into `conflict_pairs`, which drives
  `CONFLICT_OPENED` and `CONFLICT_RESOLVED`. Those events are the ledger's record of open
  contradiction and gate permit consumption.

**Contradiction detection becomes incremental**, because unlike relabeling its inputs are immutable.
`candidate_pair` and `classify_deterministically` read only `content` and `qualifiers`. No projection
handler ever updates those columns — `beliefs` is updated only for `status`, `admission_status`,
`observed_at` and `corroboration` — so a pair's deterministic verdict is a pure function of data that
cannot change after admission. Re-deriving it every ingestion is provably redundant work, and only
pairs involving a belief admitted since the previous pass can produce a verdict not already reached.

**Cheaper, semantically neutral reductions come first.** `_after_new_beliefs` loads the full belief
set three times and the source set twice per ingestion. Threading one loaded snapshot through the
three phases changes no behaviour and removes most of the redundant I/O; it should be measured before
any algorithmic change is attempted.

Two constraints bind the incremental work:

- The semantic-candidate selection is order-dependent. Today `_detect_deterministic_rebuts` offers
  the model the first `uncertain` pair in sorted order whose payload hash is absent from
  `component_verdict_input_hashes`. Narrowing the scanned set changes which pair is offered, and that
  is a behavioural change even though it is not a correctness one. It must be preserved by keeping a
  full-order scan for selection, or re-specified deliberately and recorded here.
- Defeat-edge deduplication must stay keyed on the persisted `defeats` rows rather than on retained
  in-memory state, so that a restart cannot reintroduce an edge.

Accepting this ADR requires a differential test that runs the incremental and whole-episode
implementations over generated episodes and asserts the **emitted event sets are identical** — kind,
aggregate and payload — not merely that final statuses agree.

## Consequences

The event stream, not the final state, is the contract under test. Relabeling and contradiction
detection do not merely compute statuses; they emit `BELIEF_STATUS_CHANGED`, `DEFEAT_ADDED`,
`RETRACTION_CREATED`, `SOURCE_STATS_DELTA` and conflict transitions into the hash-chained log. A
missed pair is not a stale cache: it is a missing `DEFEAT_ADDED`, and therefore a belief left `IN`
that should have been contested and could go on to satisfy an action precondition. That is why the
gate compares events rather than outcomes.

Replay is unaffected either way. `LedgerStore.replay` re-applies stored events through `apply_event`
and never re-runs the engine, so no change here can alter the projection hash of existing history.
The exposure is confined to newly generated events, which is precisely where a differential test can
observe it.

Leaving the fixed point whole keeps `engine/defeat.py` the single deterministic definition of
relabeling, which the property tests and the specification's saṃśaya semantics are written against.
Splitting it into a full and an incremental path would double that surface and require both to be
proven equivalent on every future change to priority, staleness or cycle handling.

The numbers give an operational threshold rather than an absolute verdict. Around 500 beliefs a
single ingestion costs roughly 96 ms, of which about 27 ms is contradiction detection and about 7 ms
is relabeling. Episodes that stay well below that are unaffected by any of this work, and the
measurement should be repeated on real episode-length distributions before the incremental path is
built.
