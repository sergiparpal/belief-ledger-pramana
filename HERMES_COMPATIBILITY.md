# Hermes compatibility

The audited adapter contract is pinned to Hermes Agent `0.18.2`, audited at commit
`3b2ef789dfcf92f5b7b18c08c59d25948e50857f` on 2026-07-11, with Python
`>=3.11,<3.14` and manifest version 1.

Full mode requires all audited hooks plus `ctx.register_middleware("llm_request", ...)`.
Older or contract-incompatible hosts enter an explicitly reported diagnostics-only mode;
they never claim enforcement. A host with the documented hooks but no request middleware
may be operated in a labeled hook-context compatibility mode only when configured.

The plugin does not monkey-patch Hermes. Its callbacks run in-process and installation is
a code-trust decision. Final transforms govern the accepted response, but a streaming UI
may already have displayed provisional tokens. If another output transformer precedes this
one, even accepted-final enforcement is unavailable. Hermes does not claim the `strict` profile.

Audited upstream sources:

- <https://github.com/NousResearch/hermes-agent/tree/3b2ef789dfcf92f5b7b18c08c59d25948e50857f>
- <https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/hermes_cli/plugins.py>
- <https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/hermes_cli/middleware.py>

| Host contract | Runtime mode | Maximum profile and claim |
|---|---|---|
| Hermes 0.18.2 + audited hooks + `llm_request` | full | `accepted_final`: pre-tool denial and accepted-final replacement, subject to transform precedence; no exclusive stream control |
| Required hooks present, request middleware absent | hook-context | `action_enforce`: visibly degraded per-turn context; no per-request freshness claim |
| Unsupported version, missing safety hooks, or unsafe Python | diagnostics-only | `observe`: diagnostics only; effectful actions are not authorized by this plugin |

Hermes reports `false` for atomic action-token consume, exclusive final-output gate, buffered
stream delivery, exact bound approval, and complete audited tool inventory. `doctor` prints this
capability snapshot, requested/effective profile, missing capabilities, downgrade reasons, and
transform precedence.

The non-blocking CI canary reports drift on Hermes `main`; it never widens the supported range.
