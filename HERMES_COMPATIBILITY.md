# Hermes compatibility

Full conformance is pinned to Hermes Agent `0.18.2`, audited at commit
`3b2ef789dfcf92f5b7b18c08c59d25948e50857f` on 2026-07-11, with Python
`>=3.11,<3.14` and manifest version 1.

Full mode requires all audited hooks plus `ctx.register_middleware("llm_request", ...)`.
Older or contract-incompatible hosts enter an explicitly reported diagnostics-only mode;
they never claim enforcement. A host with the documented hooks but no request middleware
may be operated in a labeled hook-context compatibility mode only when configured.

The plugin does not monkey-patch Hermes. Its callbacks run in-process and installation is
a code-trust decision. Final transforms govern the accepted response, but a streaming UI
may already have displayed provisional tokens. If another output transformer precedes this
one, strict final-output enforcement is not claimed.

Audited upstream sources:

- <https://github.com/NousResearch/hermes-agent/tree/3b2ef789dfcf92f5b7b18c08c59d25948e50857f>
- <https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/hermes_cli/plugins.py>
- <https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/hermes_cli/middleware.py>

| Host contract | Runtime mode | Action/output claim |
|---|---|---|
| Hermes 0.18.2 + audited hooks + `llm_request` | full | strict plugin-level enforcement, subject to documented transform precedence |
| Required hooks present, request middleware absent | hook-context | visibly degraded per-turn context; no per-request freshness claim |
| Unsupported version, missing safety hooks, or unsafe Python | diagnostics-only | diagnostics only; effectful actions are not authorized by this plugin |

The non-blocking CI canary reports drift on Hermes `main`; it never widens the supported range.
