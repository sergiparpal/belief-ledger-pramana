# 0012 — Model calls are attributed, and divergence is queryable

- **Status:** accepted, 2026-08-10
- **Constrains** [event compatibility](../event-compatibility.md) by adding one record kind.

## Context

The model component is non-deterministic and nothing recorded made that detectable. `LlmUsage`
captured provider, model, tokens and latency; `ComponentVerdict` captured an input digest over the
free text and an outcome. Neither captured what the model *answered*, so two runs of the same
input producing different structured results left no trace that they differed.

Two things were true at once, and the plan anticipated only one of them. The port cannot carry
sampling parameters — `StructuredModelRequest` had no field for them — but both `HostLlmClient`
implementations were already passing `temperature=0.0` to their host, hardcoded, in two separate
places. Sampling was neither host-controlled nor configurable nor recorded: it was a literal.

## Decision

### Sampling is a policy, expressed once

`SamplingPolicy` is a frozen dataclass with `temperature: float = 0.0`, validated to `[0.0, 2.0]`
at construction. It is an additive, defaulted field on `StructuredModelRequest`, so an existing
`StructuredModelPort` implementation keeps working unchanged. `verification.sampling_temperature`
configures it, with the same bounded validation the other verification settings use, and both
clients build it through one helper so the default lives in one place.

There is no `seed`. Neither the Hermes facade nor the port accepts one, and a knob that is recorded
but never applied would misrepresent what was asked of the provider.

`temperature=0.0` reduces non-determinism. It does not remove it: batching, model routing and
provider-side changes are all outside this process. That is why the rest of this record exists.

### Attribution is a sibling record, not a new field

Every call writes `LLM_CALL_ATTRIBUTION` alongside the existing usage and verdict records,
carrying provider, model, `prompt_hash`, `input_hash`, `output_hash`, the applied sampling policy,
outcome and turn.

It is a **new record kind** rather than fields on `ComponentVerdict` or `LlmUsage`. Both of those
appear in `tests/fixtures/v1_replay/`, and adding a required field to either would move hashes that
those fixtures pin as a product invariant. `LLM_CALL_ATTRIBUTION` appears in no v1 fixture, so it is
hash-neutral by construction — this is the option the plan marks preferred, and R2 of
`docs/plan-baseline.md` is what confirms the fixtures allow it. The alternative, an
additive-optional field with a `payload_schema_version` bump and an upcaster, would have bought
nothing here and cost an upcaster to maintain forever.

No projection table is added. The event log is authoritative, the query is an operator command
rather than a hot path, and a projection would mean a schema migration for something that answers
a question about history.

### `prompt_hash` is the prompt's digest

`llm/prompts.py` describes itself as "Versioned concise instructions" and carries no version
constant. Rather than invent a parallel numbering scheme — a second thing to keep in step, and one
that a prompt edit could silently fail to bump — the prompt is identified by digesting its own
text. That cannot drift from the prompt. Recorded as F-14.

### `input_hash` is stricter than `ComponentVerdict.input_hash`

`ComponentVerdict.input_hash` digests the free text alone, because adapters compare against it
through `component_verdict_input_hashes`; that contract is unchanged. Divergence needs more: two
calls with the same text but a different schema or token ceiling are not the same input, and
grouping them would report a difference in the *request* as a difference in the *model*. The
divergence digest therefore covers instructions, redacted text, schema name and `max_tokens`.

Both digests redact before hashing, so neither commits to a credential.

### The query

```
hermes belief-ledger llm-divergence [--episode EP_ID] [--json]
```

Groups recorded calls by `(prompt_hash, input_hash)` and reports every group holding more than one
distinct `output_hash`, with model label, timestamps and event IDs. Failed calls carry no output
and are excluded: an error is the absence of an answer, not a second one, and counting it would
report every transient timeout as model non-determinism.

## Consequences

- Non-determinism becomes an audit rather than a caveat. It is still non-determinism — this detects
  divergence after the fact and does not prevent it.
- One existing assertion changed. `tests/core/test_core_services.py` asserted that a successful
  call emits two events; it now asserts the three kinds by name, which says what they are rather
  than how many. Recorded as F-15.
- Every component call writes a third event. On the fixtures in this repository that is a small
  constant increase in log volume, and it is the same order as the usage record already written.
- The two `HostLlmClient` implementations both had to change, in step. That duplication is the
  plan's §5.3 and is Stage 7's problem; the digest computation lives in one shared module precisely
  so the two cannot drift, because a drifted digest would report an adapter difference as a model
  divergence.
