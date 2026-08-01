# Python API

The supported primary imports are exported from `belief_ledger_core`: `BeliefLedger`, `CoreConfig`,
`RuntimeDependencies`, `EpisodeContext`, `EvidenceObservation`, `ToolDescriptor`,
`ToolPolicyManifest`, `ToolInvocation`, `ApprovalResult`, `OutputCandidate`, and their immutable
result values. Every request/result carries `schema_version` and every failure has a stable
`reason_code` or `BeliefLedgerError.reason_code`.

`BeliefLedger.open()` requires an explicit state root and accepts configuration values, deterministic
dependency ports, caller capabilities, a requested profile, and a versioned policy manifest.
`start_episode()` and `finalize_episode()` own lifecycle. `ingest_evidence()` is the generic path;
user, tool-result, direct-observation, and derived-evidence helpers only normalize into it.

`evaluate_action()` decides and may issue an opaque in-process permit. It does not execute.
Effectful adapters call `consume_permission()` atomically immediately before private handler lookup.
Argument, target, turn, namespace, policy, configuration, expiry, support, conflict, or approval
drift fails closed. A consumed permit is not restored after handler failure.

`record_approval()` is trusted adapter/control-plane input, not authentication and not a model tool.
The caller authenticates the approving actor and channel before constructing `ApprovalResult`; core
persists and validates the exact binding. Denial or mismatch cannot authorize.

`evaluate_output()` returns bytes to deliver or a deterministic block report but owns no sink.
Adapters that claim buffered delivery must use an exclusive sink, such as the strict reference
runner. `query()`, `explain_decision()`, `verify_chain()`, `replay()`, and `export_episode()` are
read/query operations. One service instance is safe to use with the connection-per-operation SQLite
store; a caller must not share opaque permits across unrelated processes or state roots.

The legacy `LedgerRuntime` remains only as a deprecated fixture facade for 1.x compatibility. New
integrations must use `BeliefLedger` and must not import through `belief_ledger_pramana`.
