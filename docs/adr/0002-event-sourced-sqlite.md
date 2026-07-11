# ADR 0002: Event-sourced SQLite

Status: accepted, 2026-07-11.

SQLite WAL is the episode store. Immutable canonical events and per-episode SHA-256 chains are the
source of truth; relational/FTS tables are deterministic projections. Every state mutation appends
events and updates projections in one immediate transaction. This gives offline operation,
auditable causes, bounded concurrency, and exact replay without a remote service.

The chain is tamper-evidence only. Database purge/compaction cannot pretend projection deletion is
data erasure and requires a separate explicit destructive workflow.

