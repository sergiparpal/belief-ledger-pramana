# Architecture

The rc2 workspace has three frozen-version distributions:

```text
belief-ledger-core (host-neutral contracts, manifests, profile negotiation,
                    action decisions, response buffering)
        ^                                      ^
        | exact 1.0.0rc2                       | exact 1.0.0rc2
belief-ledger-pramana                    belief-ledger-reference
(Hermes adapter + v1 ledger)             (strict runner + JSONL protocol)
```

Core never imports Hermes. Both adapters normalize lifecycle identifiers and injected dependencies
before calling core. The Hermes package keeps its historical entry point, directory/Git layout,
state path, events, and v1 projection hash. The reference package owns an in-process tool registry
and delivery sink so it can prove strict dispatch and output guarantees.

Inside the Hermes adapter, `PluginRuntime` remains a compatibility facade over application use
cases and small ledger/LLM ports. SQLite infrastructure implements those ports without allowing a
database transaction or process lock to span a provider call, approval wait, or external handler.

The domain ledger remains an append-only per-episode SHA-256 chain. `BEGIN IMMEDIATE` appends a
batch and applies projections atomically. Explicit manifests, hash verification, and replay protect
compatibility. A private HMAC integrity key authenticates event hashes separately from the public
chain. Authorization uses a second append-only enforcement chain and rebuildable
`approval_receipts`/`action_decisions`; schema v6 installs them without changing v1 projection
material.

```text
normalize invocation -> policy/preconditions -> exact approval receipt
-> opaque bound action decision -> BEGIN IMMEDIATE consume + event
-> invoke private handler -> ingest result
```

The raw token exists only in process. A crash after consume is fail-safe at-most-once authorization;
SQLite and an external effect do not form a distributed transaction. Strict output buffers ordered
bytes, validates complete UTF-8 and lint policy, prepares the owned sink, then attempts one delivery
of accepted bytes or the deterministic block report.

The justification graph is acyclic on write while REBUT/UNDERCUT edges may cycle. Relabeling uses
live supports, justification premises, visible lexicographic priority, structural retraction, and
fixed-point reinstatement. No lock or transaction spans a provider call, approval wait, or external
handler.
