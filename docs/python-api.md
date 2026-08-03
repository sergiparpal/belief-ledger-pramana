# Python API

The supported primary imports are exported from `belief_ledger_core`. New integrations should use
`BeliefLedger` and the immutable request/result values from that package rather than importing
adapter internals.

## Minimal lifecycle

```python
from pathlib import Path

from belief_ledger_core import (
    BeliefLedger,
    EpisodeContext,
    EvidenceObservation,
    ToolInvocation,
)

ledger = BeliefLedger.open(state_root=Path(".belief-ledger"))
context = EpisodeContext.normalize(
    session_id="session-1",
    turn_id="turn-1",
    task_id="notify-customer",
    platform="my-adapter",
)
episode = ledger.start_episode(context)

admission = ledger.ingest_evidence(
    episode.id,
    EvidenceObservation.normalize(
        "Customer customer-42 is the intended recipient",
        source_name="customer-directory",
        source_kind="tool",
        source_integrity="trusted",
        target="customer-42",
    ),
)
authorization = ledger.evaluate_action(
    episode.id,
    ToolInvocation.normalize(
        context,
        "send_customer_message",
        {"recipient": "customer-42", "body": "Your replacement shipped."},
        namespace="crm",
    ),
)
ledger.finalize_episode(episode.id)
```

The packaged default manifest contains only reviewed read-only patterns, so the caller-defined
message action above blocks unless the caller supplies an explicit matching manifest. Evidence
admission does not itself authorize an action. This example is adapter-side code: assign
`source_integrity="trusted"` only after authenticating and auditing the source; never copy a trust
label supplied by a model or another untrusted caller.

## Construction and configuration

`BeliefLedger.open()` requires an explicit state root and accepts configuration values, deterministic
dependency ports, caller capabilities, a requested profile, and a versioned policy manifest.
Core merges only explicitly passed `CoreConfig`/mapping values over packaged defaults. It never
searches for `config.yaml`, `policies.json`, a Hermes profile, or another host-owned location; the
gateway and each adapter resolve their own files before calling core.

The default requested profile is `observe` and the default `HostCapabilities` proves no owned host
boundary. Requesting `action_enforce`, `accepted_final`, or `strict` without the matching audited
capabilities fails with `CAPABILITY_SHORTFALL`. Capabilities describe construction, not a desired
label.

## Lifecycle, evidence, and decisions

`start_episode()` and `finalize_episode()` own lifecycle. `ingest_evidence()` is the generic path;
user, tool-result, direct-observation, and derived-evidence helpers only normalize into it.

`evaluate_action()` decides and may issue an opaque in-process permit. It does not execute.
Effectful adapters call `consume_permission()` atomically immediately before private handler lookup.
Argument, target, turn, namespace, policy, configuration, expiry, support, conflict, or approval
drift fails closed. A consumed permit is not restored after handler failure.

Consumption re-reads episode, support, and conflict state inside the authorization transaction.
Three rules follow from that, and each refuses the permit and revokes it permanently:

- the episode must still be `active`; a permit bound to a finalized episode is refused with
  `EPISODE_FINALIZED` whether or not finalization's revocation ran
- every supporting belief named by the binding must still be `in`, otherwise `SUPPORT_RETRACTED`
- the episode must have **no** open conflict, otherwise `OPEN_CONFLICT`

The conflict rule is deliberately episode-wide rather than limited to the permit's own
`blocking_conflict_ids`. A conflict opened after the permit was issued is exactly the case the
binding could not have named, so an unrelated open conflict in the same episode blocks consumption.
Resolve or close conflicts before consuming.

`record_approval()` is trusted adapter/control-plane input, not authentication and not a model tool.
The caller authenticates the approving actor and channel before constructing `ApprovalResult`; core
persists and validates the exact binding. Denial or mismatch cannot authorize.

An approval binds the normalized namespace/tool, turn, target, policy ID/revision, and the SHA-256
digest of canonical invocation arguments (UTF-8 JSON with sorted keys, compact separators, and no
NaN). It does not mean “approve whatever the agent does next.” Raw permits are process-local,
redacted from representations, stored only as digests, and accepted only by
`consume_permission()`; do not serialize them into JSONL, MCP, logs, or durable queues.

## Output and queries

`evaluate_output()` returns bytes to deliver or a deterministic block report but owns no sink.
Adapters that claim buffered delivery must use an exclusive sink, such as the strict reference
runner. `query()`, `explain_decision()`, `verify_chain()`, `replay()`, and `export_episode()` are
read/query operations. One service instance is safe to use with the connection-per-operation SQLite
store; a caller must not share opaque permits across unrelated processes or state roots.

Every public request/result carries `schema_version`. Decision-style failures have a stable
`reason_code`; exceptional API failures use `BeliefLedgerError.reason_code`. The primary public
surface also exports `CoreConfig`, `RuntimeDependencies`, `ToolDescriptor`, `ToolPolicyManifest`,
`ApprovalResult`, `OutputCandidate`, and the immutable result types needed for these operations.

The legacy `LedgerRuntime` remains only as a deprecated fixture facade for 1.x compatibility. New
integrations must use `BeliefLedger` and must not import through `belief_ledger_pramana`.
