# Host-neutral quickstart

This source-checkout path does not require an agent host. It requires Python 3.11–3.13 and
[`uv`](https://docs.astral.sh/uv/); the workspace distributions are local release candidates and
are not presented as public-registry installs.

## Run the offline examples

```console
uv sync --frozen --all-packages --group dev
uv run --no-sync belief-ledger demo --format json
uv run --no-sync python examples/custom_tool_gate/run.py --format json
```

The `demo` command runs an `observe`-profile decision in a temporary directory and leaves no
durable product state. Its effectful example blocks because recipient evidence is missing. The
custom-tool example defines a CRM message tool, schema, effect classification, policy, and private
handler. It proves block → trusted evidence → exact approval → single execution → support
retraction → block through the strict reference runner.

## Initialize durable local state

```console
uv run --no-sync belief-ledger --state-root .belief-ledger init --format json
uv run --no-sync belief-ledger --state-root .belief-ledger policy validate --format json
uv run --no-sync belief-ledger --state-root .belief-ledger ledger verify-chain --format json
```

`init` creates a private state directory containing `config.yaml`, `policies.json`,
`ledger.sqlite3`, and `.ledger.integrity.key`. On POSIX, the directory is mode `0700` and the files
are mode `0600`. The integrity key authenticates ledger events and must be backed up and restored
with the matching database; it is not an export or configuration value. See
[operations](operations.md) before copying an active database.

The neutral CLI always uses the state root supplied to the process. It does not inspect Hermes
profiles. The `BeliefLedger` Python API is even more explicit: it does not automatically load the
gateway's `config.yaml` or `policies.json`; callers pass configuration and manifests themselves.

## Review policy before enforcement

Policy scaffolds are inactive and use `REVIEW_REQUIRED`; review the effect classification, JSON
Schema digest, target fields, preconditions, integrity threshold, and approval policy before
activation.

```console
uv run --no-sync belief-ledger --state-root .belief-ledger policy scaffold send_customer_message --namespace crm --format json
uv run --no-sync belief-ledger --state-root .belief-ledger policy explain send_customer_message --namespace crm --format json
uv run --no-sync belief-ledger --state-root .belief-ledger policy inventory --format json
```

`scaffold` prints a candidate; it does not activate or write it. Edit `policies.json` only after
review, then run `policy validate`. The standalone CLI has no connected tool inventory, so
`policy inventory` deliberately reports `complete: false`; an enforcing adapter must supply and
classify its complete inventory itself.

See [Python API](python-api.md) for programmatic composition, [gateway protocol](gateway-protocol.md)
for the local JSONL decision boundary, and [product surfaces](product-surface.md) before selecting
an enforcement profile.

Repository scripts create local artifacts only. They do not publish packages.
