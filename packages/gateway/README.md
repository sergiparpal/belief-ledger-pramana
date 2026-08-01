# belief-ledger-gateway

Evidence-backed policy enforcement for AI agents.

This distribution owns the host-neutral `belief-ledger` command, a local versioned JSONL decision
service, and an optional in-process dispatcher. JSONL reports `observe`: callers can bypass it and
execute an action themselves. The in-process dispatcher reports `action_enforce` only when it owns
the private handler registry and consumes a bound permit immediately before lookup and execution.

| Surface | Maximum profile | Enforcement boundary |
|---|---|---|
| JSONL decision service | `observe` | Returns decisions; the client still owns execution. |
| `GatewayDispatcher` | `action_enforce` | Owns a private handler registry and consumes immediately before dispatch. |

From a source checkout:

```console
uv run --no-sync belief-ledger demo --format json
uv run --no-sync belief-ledger --state-root .belief-ledger init
```

See the [gateway protocol](../../docs/gateway-protocol.md) for the wire contract and the
[host-neutral quickstart](../../docs/quickstart.md) for state initialization and policy review.
Repository scripts build local artifacts but do not publish this distribution.
