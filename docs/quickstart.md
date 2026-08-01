# Host-neutral quickstart

This source-checkout path does not require an agent host.

```console
uv sync --frozen --all-packages --group dev
uv run --no-sync belief-ledger demo --format json
uv run --no-sync python examples/custom_tool_gate/run.py --format json
uv run --no-sync belief-ledger --state-root .belief-ledger init --format json
uv run --no-sync belief-ledger --state-root .belief-ledger policy validate --format json
uv run --no-sync belief-ledger --state-root .belief-ledger ledger verify-chain --format json
```

The demo is decision-only and reports `observe`. The custom-tool example defines a CRM message tool,
schema, effect classification, policy, and private handler; it proves block → trusted evidence →
exact approval → single execution → support retraction → block through the strict reference runner.

Policy scaffolds are inactive and use `REVIEW_REQUIRED`; review the effect classification, JSON
Schema digest, target fields, preconditions, integrity threshold, and approval policy before
activation. Use `belief-ledger policy explain TOOL --format json` to inspect a match. See
[Python API](python-api.md) for programmatic composition and [gateway protocol](gateway-protocol.md)
for a local process boundary.

Repository scripts create local artifacts only. They do not publish packages.
