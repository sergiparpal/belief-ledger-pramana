# Belief Ledger Pramana

Evidence-backed policy enforcement for AI agents.

Belief Ledger is the evidence-backed authorization layer between agents and consequential actions.
It requires current evidence, reviewed policy, and exact approval at the boundary that owns an
effect. Adapters expose only the guarantees their construction can prove.

## One concrete decision

```json
{
  "outcome": "block",
  "reason_code": "MISSING_PRECONDITION",
  "missing": ["Precondition recipient_identity holds for customer-42"],
  "executed": false
}
```

The action has no trusted evidence binding the intended recipient. The safe next step is a
read-only directory observation, followed—if policy requires it—by an authenticated approval bound
to the exact namespace, tool, arguments, target, policy revision, and turn.

## How it works

Belief Ledger follows one neutral sequence: observe evidence → admit a normalized belief → evaluate
policy → issue a bound permit or block → atomically consume at an adapter-owned boundary → audit
and retract when support changes. Core never executes an arbitrary callback. A decision service is
not described as enforcement unless an adapter owns the only dispatch path.

## Host-neutral quickstart

From a source checkout, synchronize all five local packages and run the offline demo:

```console
uv sync --frozen --all-packages --group dev
uv run --no-sync belief-ledger demo --format json
uv run --no-sync python examples/custom_tool_gate/run.py --format json
```

Initialize durable local state and inspect it without any agent host:

```console
uv run --no-sync belief-ledger --state-root .belief-ledger init --format json
uv run --no-sync belief-ledger --state-root .belief-ledger ledger verify-chain --format json
```

See [the quickstart](docs/quickstart.md) for a first policy and explanation. These commands operate
from the source workspace; repository tooling does not publish packages to a public registry.

## Choose an interface

| Interface | Purpose | Honest maximum in this repository |
|---|---|---|
| [Python core](docs/python-api.md) | Generic lifecycle, evidence, decisions, permits, output evaluation, query, replay | Selected from caller-owned capabilities; core itself does not execute |
| [Gateway](docs/gateway-protocol.md) | Neutral CLI, local JSONL decisions, optional owned dispatcher | `observe` JSONL; `action_enforce` owned dispatcher |
| [MCP](docs/integrations/mcp.md) | Inspection resources or complete-inventory wrapped tools | `observe` inspection; at most `action_enforce` proxy |
| [Hermes](docs/integrations/hermes.md) | Backward-compatible audited host adapter | `accepted_final` |
| [Reference](docs/adapter-conformance.md) | Deterministic conformance evidence with a private registry and sink | `strict` |

## Capability profiles

- `observe` produces evidence and decisions but does not own execution or delivery.
- `action_enforce` owns a pre-action dispatch boundary and blocks before an effect.
- `accepted_final` also owns transformation of the accepted final response, while provisional
  streaming or another sink may remain outside its control.
- `strict` additionally proves complete inventory, exact approvals, atomic single-use consumption,
  exclusive output gating, and buffered delivery through one owned sink.

Profiles are construction claims, not configuration labels. Missing capabilities fail closed or
produce an explicit diagnostic downgrade; they are never inferred from a server returning “allow.”

## Python API

```python
from pathlib import Path
from belief_ledger_core import (
    BeliefLedger,
    EpisodeContext,
    EvidenceObservation,
    ToolInvocation,
)

ledger = BeliefLedger.open(state_root=Path(".belief-ledger"))
context = EpisodeContext.normalize(session_id="s-1", turn_id="t-1", task_id="notify")
episode = ledger.start_episode(context)
ledger.ingest_evidence(
    episode.id,
    EvidenceObservation.normalize(
        "Customer customer-42 is the intended recipient",
        source_name="customer-directory",
        source_kind="tool",
        source_integrity="trusted",
        target="customer-42",
    ),
)
decision = ledger.evaluate_action(
    episode.id,
    ToolInvocation.normalize(
        context,
        "send_customer_message",
        {"recipient": "customer-42", "body": "Your replacement shipped."},
        namespace="crm",
    ),
)
```

The default manifest intentionally knows only reviewed read-only patterns, so the caller-defined
message action blocks until an explicit manifest is supplied. `record_approval()` is a trusted
control-plane operation: the adapter must authenticate the approving actor and channel first. It
is not exposed through JSONL or MCP model tools. The snippet is adapter-side code: assign
`source_integrity="trusted"` only after authenticating and auditing the source, never from a
model-supplied trust label.

## Operations and security

Durable state is append-only and hash chained; action authorization uses a separate append-only
enforcement chain. Raw permits are stored only as digests, expire, bind every action dimension,
and are single-use. Provider calls, approval waits, handlers, and network requests run outside
database transactions. Evidence is redacted before persistence under the configured retention
mode.

Read [operations](docs/operations.md), [configuration](docs/configuration.md), the
[threat model](docs/threat-model.md), [event compatibility](docs/event-compatibility.md), and
[upgrade/rollback](docs/upgrade-and-rollback.md). Belief Ledger is not a sandbox.
It is not a universal compliance system or prompt-injection defense. External effects are
not exactly-once.

## Integrations

The [MCP integration](docs/integrations/mcp.md) documents inspection and proxy modes next to its
direct-upstream bypass warning. The [Hermes integration](docs/integrations/hermes.md) retains the
1.x `belief-ledger-pramana` distribution, import package, plugin entry point, profile-local paths,
and audited Hermes Agent `0.19.0` contract at commit
`3ef6bbd201263d354fd83ec55b3c306ded2eb72a`. Hermes remains a peer host, not a core dependency.

## Why Pramana?

*Pramāṇa* is a Sanskrit term for a means or source of reliable knowledge. This repository retains
the term for its provenance model and its backward-compatible Hermes package: direct observation,
testimony, derived inference, explanatory inference, analogy, and qualified absence have different
admission and defeat rules. “Belief Ledger” is the first-use product name; `pramana` remains the
ASCII compatibility spelling.

## Development and release status

```console
uv run --no-sync python scripts/verify_stage.py all --skip-build
uv run --no-sync python scripts/verify_stage.py all
```

The workspace builds synchronized `1.0.0rc4` local distributions for core, gateway, reference,
MCP, and the Hermes adapter. The supported Python range is `>=3.11,<3.14`, and CI runs the matrix
across every version in it. Builds, checks, and smoke installs do not publish, sign, tag, push, or
open a pull request. GitHub release `v0.2.1` records the complete RC4 source state but does not
publish the distributions to a package registry or upload built wheels or sdists. See
[CHANGELOG.md](CHANGELOG.md), [RELEASE_NOTES.md](RELEASE_NOTES.md), and
[the product-surface ADR](docs/adr/0007-host-neutral-product-surface.md). The
[decision records](docs/adr/README.md) index every decision that constrains the implementation, and
[open findings](docs/open-findings.md) records what is known to be wrong or unproven and is not
fixed, with the evidence for each.
