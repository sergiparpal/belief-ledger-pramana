# MCP integration

Evidence-backed policy enforcement for AI agents.

## Bypass warning

Connecting directly to the upstream MCP server bypasses the Belief Ledger proxy. Keep upstream
transport credentials and endpoints unavailable to the model-facing client if proxy enforcement is
required. MCP tool proxying does not own final model-response delivery, so it never claims
`accepted_final` or `strict`.

Inspection mode exposes capabilities, policies, episode beliefs/conflicts/decisions/audit, bounded
query, decision explanation, untrusted premise-bound inference recording, and chain verification.
It reports `observe`. There is no model-callable approval, trusted-provenance assignment, policy
mutation, purge, raw-token consumption, or enforcement-disable tool.

Proxy mode requires a complete upstream inventory and explicit manifest coverage. It normalizes
each upstream schema into `ToolDescriptor`, records schema digests, and fails closed for incomplete
inventory, unknown/ambiguous policy, drift, correlation loss, mutated binding, expiry, replay,
retracted support, conflict reopening, or upstream failure. Read-only tools forward directly.
Effectful tools evaluate and atomically consume an internal permit immediately before forwarding.
Raw upstream bytes are returned unchanged; separately redacted evidence is ingested.

| Mode | Profile | Owned boundary | Not owned |
|---|---|---|---|
| Inspection | `observe` | bounded ledger reads | execution and output delivery |
| Proxy | at most `action_enforce` | wrapped upstream tool dispatch | direct-upstream access and final output |

From a source checkout, inspection mode uses the official Python MCP SDK 2.x over stdio:

```console
uv run --no-sync belief-ledger-mcp --state-root .belief-ledger --mode inspection
```

Proxy mode is constructed programmatically with an authenticated `UpstreamClient`; the CLI refuses
an unconfigured proxy so inventory completeness cannot be guessed. `list_tools()` returns immutable
`UpstreamTool` records, and `call_tool()` returns
`UpstreamCallResult(schema_version=1, content=bytes, is_error=bool, status=str)`. A reported upstream
error, malformed result, correlation loss, or result larger than the default 1 MiB bound fails
closed. Wrapper names are derived injectively with `proxy_tool_name(namespace, name)`, so distinct
namespace/name pairs cannot alias after MCP-safe normalization.
