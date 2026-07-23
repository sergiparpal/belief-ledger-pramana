# ADR 0005: Host-neutral core

Status: accepted, 2026-07-22.

`belief-ledger-core` owns normalized lifecycle values, deterministic dependencies, evidence and
provenance, event storage and replay, projections, reasoning, policy manifests, action/output
decisions, verification ports, and audit/evaluation primitives. It receives an explicit state-root
`Path` and may not import Hermes packages, constants, callback dictionaries, or request shapes.

Adapters normalize episode/turn context, tool invocation/result, approval, inventory, and output
candidates. They own host inspection, configuration-root precedence, callback registration,
request mutation, tool dispatch, approval UI, streaming, and final delivery. Adapter diagnostics
remain outside core values.

| Existing area | Owner |
|---|---|
| models, events, IDs, store, migrations, projections | core |
| engine, normalized ingestion, context selection/rendering, gate/lint/verification | core |
| structured-model budget/reservation wrapper | core |
| compatibility inspection and Hermes home resolution | Hermes adapter |
| request injection and provider/Hermes callback translation | Hermes adapter |
| plugin, hooks, middleware, tools, slash/operator commands | Hermes adapter |

The 1.x compatibility distribution remains `belief-ledger-pramana`, import package
`belief_ledger_pramana`, and entry point `belief_ledger_pramana.plugin`. Compatibility re-exports
are retained where baseline docs/tests establish public use. The standalone reference adapter is a
second distribution. All three packages use version `1.0.0rc2`; adapters require exactly the same
core candidate. Development workspace source overrides must not leak into built metadata.
