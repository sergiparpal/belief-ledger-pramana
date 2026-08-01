# Hermes compatibility

This supported compatibility path retains the canonical audited matrix. New adopters should start
with the adapter landing page at [docs/integrations/hermes.md](docs/integrations/hermes.md); the
host-neutral product quickstart is in [docs/quickstart.md](docs/quickstart.md).

The audited adapter contract is pinned to Hermes Agent `0.19.0`, audited at commit
`3ef6bbd201263d354fd83ec55b3c306ded2eb72a` on 2026-07-23, with Python
`>=3.11,<3.14` and manifest version 1.

Hermes `0.19.0` pins Pillow `12.2.0` and cryptography `46.0.7`, versions with published security
advisories. The tested install contract therefore installs the exact Hermes host and then overrides
those leaves with `Pillow>=12.3,<13` and `cryptography>=48.0.1,<50`. CI, clean-install smoke tests,
and dependency audit all use that same sequence until Hermes relaxes its metadata. Pip may report
the host's exact metadata pins as incompatible; do not downgrade the remediated leaves to silence
that warning.

Full mode requires all audited hooks plus `ctx.register_middleware("llm_request", ...)`.
Older or contract-incompatible hosts enter an explicitly reported diagnostics-only mode;
they never claim enforcement. A host with the documented hooks but no request middleware
may be operated in a labeled hook-context compatibility mode only when configured.

The plugin does not monkey-patch Hermes. Its callbacks run in-process and installation is
a code-trust decision. Final transforms govern the accepted response, but a streaming UI
may already have displayed provisional tokens. If another output transformer precedes this
one, even accepted-final enforcement is unavailable. Hermes does not claim the `strict` profile.

Audited upstream sources:

- <https://github.com/NousResearch/hermes-agent/tree/3ef6bbd201263d354fd83ec55b3c306ded2eb72a>
- <https://github.com/NousResearch/hermes-agent/blob/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/hermes_cli/plugins.py>
- <https://github.com/NousResearch/hermes-agent/blob/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/hermes_cli/middleware.py>

| Host contract | Runtime mode | Maximum profile and claim |
|---|---|---|
| Hermes 0.19.0 + audited hooks + `llm_request` | full | `accepted_final`: pre-tool denial and accepted-final replacement, subject to transform precedence; no exclusive stream control |
| Required hooks present, request middleware absent | hook-context | `action_enforce`: visibly degraded per-turn context; no per-request freshness claim |
| Unsupported version, missing safety hooks, or unsafe Python | diagnostics-only | `observe`: diagnostics only; effectful actions are not authorized by this plugin |

Hermes reports `false` for atomic action-token consume, exclusive final-output gate, buffered
stream delivery, exact bound approval, and complete audited tool inventory. `doctor` prints this
capability snapshot, requested/effective profile, missing capabilities, downgrade reasons, and
transform precedence.

The non-blocking CI canary reports drift on Hermes `main`; it never widens the supported range.
