# After installation

Belief Ledger provides evidence-backed policy enforcement for AI agents through this audited
Hermes adapter.

Enable the opt-in plugin, then restart the current Hermes or gateway process:

```bash
hermes plugins enable belief-ledger-pramana --no-allow-tool-override
hermes belief-ledger doctor
```

Project-directory plugins additionally require `HERMES_ENABLE_PROJECT_PLUGINS=1`.
`HERMES_SAFE_MODE=1` disables all plugin discovery.
