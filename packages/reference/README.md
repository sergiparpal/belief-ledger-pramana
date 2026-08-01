# belief-ledger-reference

Evidence-backed policy enforcement for AI agents.

This deterministic in-process adapter is conformance evidence, not a production remote-execution
service. `ReferenceRunner` starts with no business-domain tools. Callers register each
`ToolDescriptor`, effect classification, policy, and private handler before start.

The runner can claim `strict` because it proves complete inventory, exact approval binding, atomic
single-use permit consumption before handler lookup, support/conflict revocation, a private registry,
and buffered output through one owned sink. Deployment and CRM examples are composed under
`examples/`; no fixture-specific operation is part of the runner API.

| Surface | Maximum profile | Enforcement boundary |
|---|---|---|
| `ReferenceRunner` | `strict` | Owns inventory, private handlers, permit consumption, buffering, and the sole sink. |

`belief_ledger_reference.ReferenceRunner` is the supported package entry point. See
[adapter authoring](../../docs/adapter-authoring.md) and
[adapter conformance](../../docs/adapter-conformance.md) before using it as a construction example.
Repository scripts build local artifacts but do not publish this distribution.
