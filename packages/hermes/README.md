# belief-ledger-pramana: Hermes adapter

Evidence-backed policy enforcement for AI agents.

The root distribution remains the backward-compatible Hermes 1.x adapter so Git/directory plugin
discovery, `belief_ledger_pramana`, and the `belief-ledger-pramana` plugin entry point remain stable.
It depends exactly on the same-candidate core and gateway; the gateway supplies the neutral
`belief-ledger` executable.

The audited host contract is Hermes Agent `0.19.0` at commit
`3ef6bbd201263d354fd83ec55b3c306ded2eb72a`. Its honest maximum is `accepted_final`, not `strict`:
it owns pre-action denial and accepted-final transformation, while complete inventory, atomic action
token consumption, bound approval, exclusive buffering, and provisional-stream visibility remain
outside the audited contract.

| Surface | Maximum profile | Enforcement boundary |
|---|---|---|
| Hermes 0.19.0 adapter | `accepted_final` | Host hooks deny actions and transform accepted final responses. |
| `strict` | Unsupported | The audited host does not prove complete inventory, atomic consume, or an exclusive buffered sink. |

State/config precedence, plugin tools, hooks, middleware, and `hermes belief-ledger ...` commands are
unchanged. Run `hermes belief-ledger doctor` after enabling. See
[`docs/integrations/hermes.md`](../../docs/integrations/hermes.md) for installation, diagnostics,
paths, upgrades, and limitations. Repository scripts do not publish the package.
