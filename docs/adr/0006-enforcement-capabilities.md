# ADR 0006: Enforcement capabilities

Status: accepted, 2026-07-22.

Capability booleans describe audited observable host behavior. `false` means absent, unknown, or
not proven; configuration wishes never turn it into `true`.

| Capability | Observable proof |
|---|---|
| `per_request_context` | every provider request receives the current normalized context |
| `pre_action_gate` | the adapter can deny an invocation before its handler runs |
| `atomic_action_token_consume` | a single-use token is consumed transactionally immediately before dispatch |
| `accepted_final_transform` | the host accepts an adapter-selected final replacement |
| `exclusive_final_output_gate` | no competing path can deliver user-visible final output |
| `buffered_stream_delivery` | the adapter withholds all high-stakes chunks until acceptance |
| `bound_approval` | approval fields prove the exact tool, arguments, target, turn, policy, and scope |
| `tool_inventory` | the adapter supplies a complete audited tool inventory and change signal |

Profiles use this exact truth table:

| Profile | Required capabilities |
|---|---|
| `observe` | none |
| `action_enforce` | `pre_action_gate` |
| `accepted_final` | `action_enforce` plus `per_request_context`, `accepted_final_transform` |
| `strict` | `accepted_final` plus `atomic_action_token_consume`, `exclusive_final_output_gate`, `buffered_stream_delivery`, `bound_approval`, `tool_inventory` |

An enforcing request with missing requirements fails closed. Diagnostic downgrade is allowed only
when explicitly configured; it changes the persisted effective profile and public label. Observe
mode reports missing capabilities without blocking.

Accepted-final transformation does not retract provisional streaming already shown. Strict
dispatch consumes authorization and appends its event atomically, then invokes the external
handler; SQLite and an external effect do not share a distributed transaction. A crash after
consume is fail-safe at-most-once authorization and the token is not reusable. Strict buffered
delivery is exclusive inside its adapter. The base sink attempts delivery at most once and is not a
durable exactly-once protocol unless the sink independently supplies idempotency.
