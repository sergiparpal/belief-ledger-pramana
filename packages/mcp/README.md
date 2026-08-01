# belief-ledger-mcp

Evidence-backed policy enforcement for AI agents.

Inspection mode exposes bounded ledger resources and safe query, explanation, inference-recording,
and chain-verification tools. It reports `observe`. Proxy mode requires a complete explicit
upstream inventory and policy coverage, keeps raw permits inside the process, consumes immediately
before forwarding, and reports at most `action_enforce`.

**Bypass warning:** connecting directly to the upstream MCP server bypasses the proxy. MCP tool
proxying does not own final model-response delivery, so this package does not claim `accepted_final`
or `strict`.

| Mode | Maximum profile | Enforcement boundary |
|---|---|---|
| Inspection | `observe` | Read/query operations only; it neither dispatches nor owns output delivery. |
| Proxy | `action_enforce` | Keeps permits internal and consumes immediately before upstream forwarding. |

The supported Python entry points are `belief_ledger_mcp.BeliefLedgerMcp` and `create_server`; the
package command is `belief-ledger-mcp` and does not compete for `belief-ledger`.

The package targets the official MCP Python SDK 2.x. Proxy clients must return an
`UpstreamCallResult` containing raw bytes, an explicit error flag, and a bounded status string;
malformed, failed, or oversized upstream results are never treated as successful tool output.

Repository scripts build local artifacts; they do not publish this distribution.
