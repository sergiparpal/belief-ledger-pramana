# ADR 0007: Host-neutral product surface

Status: accepted

## Decision

The display product name is **Belief Ledger** and the approved headline is
**“Evidence-backed policy enforcement for AI agents.”** The public decision API lives in
`belief-ledger-core`; the host-neutral executable and local JSONL service live in
`belief-ledger-gateway`; `belief-ledger-reference` is deterministic conformance evidence; and the
root `belief-ledger-pramana` distribution remains the backward-compatible Hermes adapter for the
1.x line.

`belief-ledger-mcp` has distinct inspection and proxy modes. Inspection reports `observe`. A proxy
that owns wrapped tool dispatch may report at most `action_enforce`; it does not own final model
output, and direct access to its upstream is a bypass.

The Pramana distribution is not renamed during 1.x because its import package, plugin entry point,
Git/directory install shape, and state paths are compatibility contracts. All release-candidate
workspace dependencies remain exact same-candidate pins because the public API and schemas are not
stable enough for mixed-version guarantees.

## Consequences

The gateway alone owns the `belief-ledger` console command. Core never imports an adapter. Adapters
may depend on core (and stable gateway APIs where useful), but dependencies never point back toward
an adapter. Capability reports describe the boundary actually owned by the running process; a
decision response is not described as enforced execution.
