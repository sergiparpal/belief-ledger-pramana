# Architecture

The plugin has a strict host boundary and a deterministic domain core.

```text
Hermes hooks/middleware
  -> PluginRuntime episode registry + immutable turn config
  -> application use cases
       -> ingestion adapters / validity-trust / justification graph / fixed-point defeat
       -> verification scheduler / context compiler / output linter / action gate / ledger queries
  -> ports (ledger reader, event writer, LLM budget ledger, host LLM)
  -> infrastructure adapters (SQLite projections, Hermes LLM, provider-shape injector)
```

`PluginRuntime` remains a compatibility facade for Hermes callbacks while use cases receive
small, client-specific ports. `LedgerStore` remains the public SQLite facade, but composition
uses separate reader, atomic event-writer, LLM-budget, and maintenance adapters. This preserves
the append-and-project transaction invariant while allowing application services to be tested or
retargeted without a SQLite dependency.

SQLite events are authoritative; all tables other than `events` and migration metadata are
rebuildable projections. A batch obtains `BEGIN IMMEDIATE`, appends canonical events, advances
the per-episode SHA-256 head, and applies projections in the same transaction. UPDATE/DELETE
triggers protect `events`. Replay verifies the chain before rebuilding and compares canonical
projection hashes.

Episode resolution is `session_id`, established `turn_id` mapping, `task_id`, then a fresh
one-shot identity. Each graph mutation is protected by a small process-local episode lock;
SQLite WAL and idempotency keys cover threads/processes. No lock or transaction spans a host
model call, tool dispatch, or approval wait.

The justification graph is acyclic on write. REBUT/UNDERCUT edges may cycle. Relabeling computes
live basic ingestion supports and derived justifications, activates attacks only from IN beliefs,
uses visible lexicographic priority traces, marks equal/cyclic attacks PENDING (saṃśaya), and
reinstates targets in the same fixed-point run after an attacker loses support.

