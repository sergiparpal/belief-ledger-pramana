# 0017 — Ablation arms the Suite A instrument cannot isolate carry no rate

- **Status:** accepted, 2026-08-19
- **Constrains** [the specification](../belief-ledger-pramana-spec-v0.1.md) §10.

## Context

The specification's §10 names five ablation configurations: flat baseline, types only, defeat only,
no compiler contract, no gate. It states the question each is meant to answer — "which component
earns its cost?" — and `evaluations/ablations.py` published a `vikalpa_rate` for all five.

Two of those rates were not measurements. `_measured_ablations` built each configuration as a
`(response, beliefs)` pair and fed it to `lint_response`, and two pairs were duplicates of two
others:

| arm | pair | equal to |
|---|---|---|
| `defeat_only` | `(baseline_response, [])` | `flat_baseline` |
| `no_gate` | `(typed_response, [belief])` | `full` |

Running the matrix gave `flat_baseline 1.0, types_only 1.0, defeat_only 1.0,
no_generation_contract 1.0, no_gate 0.0, full 0.0`. A reader comparing `no_gate` against `full` saw
the action gate contribute exactly zero, and comparing `defeat_only` against `flat_baseline` saw the
defeat engine contribute exactly zero. Both are what an identity always shows, and neither is what
those components do.

This is not a copy-paste slip that a corrected pair would fix. `lint_response(response, beliefs)`
has exactly two inputs, so a configuration can only differ in the response text or the belief list.
The defeat engine changes belief *status* during an episode and the action gate decides whether a
tool call proceeds; neither is an input to the linter. **The Suite A instrument cannot isolate
either component, at any pair of arguments.**

The evaluation surface already knew this and said so in passing: the `method` string ended with
"Suite C separately measures gate safety" while the table above it still published a `no_gate` rate.

## Decision

`defeat_only` and `no_gate` remain enumerated, because §10 names them and dropping them would make
the report's configuration list disagree with the specification. They now report
`"vikalpa_rate": null, "measurable": false` together with a `reason` naming the suite that does
measure the component: Suite B for defeat (wrong winners, descendant propagation), Suite C for the
gate (unsafe actions reaching the handler, false-block rate).

`_measured_ablations` computes only the four arms whose pairs are distinct, and asserts its scenario
set equals `MEASURED_ARMS` so a future arm cannot be added without a distinct pair.

## Consequences

`vikalpa_rate` becomes nullable in the report schema. Consumers must read `measurable` before
comparing rates; a consumer that differences two arms blindly now gets a type error instead of a
silent zero, which is the better failure.

The published evaluation loses two numbers and gains a stated limit. That is a net gain in
information: a rate that is an identity carries none, while "this instrument cannot answer this"
tells a reader exactly what to build if they want the answer.

An ablation that reuses the Suite B and Suite C instruments to answer the two remaining questions is
possible and is not attempted here. It is design work — it needs a comparable outcome measure across
three suites that currently report different quantities — rather than the accounting fix this is.

## Alternatives rejected

**Delete the two arms.** Simpler, and it was the first plan. Rejected because §10 enumerates five
configurations and the report is the artifact that answers §10; a silently shorter list is the same
class of documentation drift that `scripts/check_doc_invariants.py` exists to prevent.

**Leave them and document the caveat in prose.** Rejected because the numbers are machine-readable
and the caveat would not be. Anything consuming the JSON would keep reading `0.0` as a measured
contribution.
