# Architecture

The current product surface has five synchronized distributions:

```text
belief-ledger-core
    ^             ^                  ^
    |             |                  |
gateway       reference          pramana/Hermes
    ^
    |
   MCP
```

Core owns domain/persistence/decisions. Gateway owns the neutral CLI, JSONL service, and optional
in-process dispatch. Reference owns deterministic strict conformance. MCP owns inspection and a
wrapped upstream boundary. The root distribution owns only Hermes compatibility orchestration.
Dependencies point upward toward core and never back into an adapter; `scripts/check_workspace_boundaries.py`
enforces static and literal dynamic imports. See [product surfaces](product-surface.md).

The rc5 dependency graph uses exact same-candidate pins:

```text
belief-ledger-core
  ^          ^             ^
  | rc5      | rc5         | rc5
gateway   reference     pramana/Hermes
  ^
  | rc5
 MCP
```

Core never imports an adapter. Every adapter normalizes lifecycle identifiers and injected
dependencies before calling core. The Hermes package keeps its historical entry point,
directory/Git layout, state path, events, and v1 projection hash. The reference package owns an
in-process tool registry and delivery sink so it can prove strict dispatch and output guarantees.

Inside the Hermes adapter, `PluginRuntime` remains a compatibility facade over application use
cases and small ledger/LLM ports. SQLite infrastructure implements those ports without allowing a
database transaction or process lock to span a provider call, approval wait, or external handler.

The domain ledger remains an append-only per-episode SHA-256 chain. `BEGIN IMMEDIATE` appends a
batch and applies projections atomically. Explicit manifests, hash verification, and replay protect
compatibility. A private HMAC integrity key authenticates event hashes separately from the public
chain. Authorization uses a second append-only enforcement chain and rebuildable
`approval_receipts`/`action_decisions`; schema v6 installs them without changing v1 projection
material. Schema v7 adds no table: it rewrites legacy unscoped `idempotency` keys into the
episode-scoped form that replay rebuilds, so a database written before that scoping can be opened
again. Schema v8 adds `snapshots`, a derived cache that is never authoritative: the append-only log rebuilds every projection, and any snapshot may be deleted at any time with no loss. The current schema version is 8, and `scripts/check_doc_invariants.py` fails if this sentence and `LATEST_SCHEMA_VERSION` disagree.

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

Admitting beliefs runs three whole-episode passes: deterministic contradiction detection,
relabeling, and passive-task completion. Relabeling is whole-episode by decision rather than by
accident — reinstatement, defeat-cycle detection, the iteration ceiling, time-driven staleness, and
equal-priority conflicts are all properties of the complete graph, so no dirty set derived from
appended events would be sound. Contradiction detection is the term that grows with episode length,
because its pairwise scan restarts from nothing on every ingestion. Measured costs, the operational
threshold they imply, and the conditions under which detection may become incremental are in
[ADR 0009](adr/0009-incremental-relabeling.md), which is proposed and has changed no code.
