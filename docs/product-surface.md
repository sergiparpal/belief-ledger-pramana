# Product surfaces

Belief Ledger is split into five same-version distributions with one-way dependencies.

| Distribution | Responsibility | May depend on | Maximum claim in this repository |
|---|---|---|---|
| `belief-ledger-core` | Domain types, event store, generic decision API, enforcement records | General-purpose runtime dependencies | Decision semantics only; selected by caller capabilities |
| `belief-ledger-gateway` | Neutral CLI, JSONL decision service, owned in-process dispatcher | Core | `observe` over JSONL; `action_enforce` for owned dispatch |
| `belief-ledger-reference` | Deterministic adapter conformance | Core | `strict` when constructed with a complete private registry and sink |
| `belief-ledger-mcp` | Inspection resources and wrapped upstream MCP tools | Core, gateway, MCP SDK | `observe` inspection; at most `action_enforce` proxy |
| `belief-ledger-pramana` | Hermes 1.x compatibility adapter | Core, gateway | `accepted_final` on the audited host contract |

Core imports no adapter. Gateway imports neither Hermes, reference, nor MCP implementation modules.
Reference imports no Hermes or MCP implementation modules. MCP imports no Hermes modules. The
Hermes adapter may use documented public core and gateway APIs.

Package versions and inter-package dependencies are synchronized exactly while the workspace is a
release candidate. Built distributions are local artifacts; repository tooling does not publish
them.
