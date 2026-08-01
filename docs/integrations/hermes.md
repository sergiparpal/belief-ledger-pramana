# Hermes integration

Evidence-backed policy enforcement for AI agents.

The `belief-ledger-pramana` distribution is the backward-compatible 1.x Hermes adapter. The audited
contract is Hermes Agent `0.19.0` at commit
`3ef6bbd201263d354fd83ec55b3c306ded2eb72a`, manifest version 1, Python `>=3.11,<3.14`. Unsupported
versions remain diagnostics-only. Hermes is a peer host rather than a Python project dependency.

For a secure supported-host install, install Hermes `0.19.0`, then override its vulnerable exact
leaf pins with `Pillow>=12.3,<13` and `cryptography>=48.0.1,<50`. The repository's CI and
clean-install smoke gate test that combination; an incompatibility warning against Hermes's stale
exact metadata is expected until upstream relaxes those pins.

| Profile | Effective | Boundary |
|---|---:|---|
| `observe` | yes | event/evidence and decisions |
| `action_enforce` | yes | pre-action denial through audited callbacks |
| `accepted_final` | maximum | accepted-response transform; provisional streaming can remain visible |
| `strict` | no | missing complete inventory, atomic token consumption, exact approval binding, and exclusive buffered delivery |

Git/directory plugin installation, `belief_ledger_pramana.plugin`, the
`hermes_agent.plugins` entry point, `hermes belief-ledger ...` commands, middleware/hooks/tools, and
profile-local state/config precedence remain unchanged. The neutral `belief-ledger` executable is
installed through the exact gateway dependency and is distinct from the Hermes command group.

State remains under `$HERMES_HOME/belief-ledger-pramana/`; `BELIEF_LEDGER_PRAMANA_CONFIG` selects an
explicit private file beneath that profile-local directory. Run `hermes belief-ledger doctor` for
adapter/core/gateway/host versions, the audited contract, requested/effective profiles, and
downgrade reasons. Detailed compatibility is also retained at [HERMES_COMPATIBILITY.md](../../HERMES_COMPATIBILITY.md).

Known limitations: plugins run with host privileges and are not a sandbox; this is not a
prompt-injection defense; another output transformer or provisional stream can bypass accepted-final
visibility; external effects are not exactly-once.
