# Authoring a Belief Ledger adapter

Implement the normalized values and ordering in [adapter-conformance.md](adapter-conformance.md)
without importing another adapter. Declare only capabilities proved by audited host behavior.

An observe adapter needs lifecycle access only. `action_enforce` requires a pre-handler deny point.
`accepted_final` additionally requires per-request context and a host-accepted final replacement;
this does not claim provisional-stream control. `strict` additionally requires a complete tool
inventory, exact bound approvals, atomic single-use decision consumption, exclusive final-output
ownership, and buffered stream delivery.

Keep handlers behind one dispatcher. Its effectful branch must consume the decision token before it
can retrieve or invoke the handler. Keep the raw token in process and persist only its SHA-256
digest. Route visible high-stakes bytes through `ResponseGate`; a direct alternate sink invalidates
the strict capability.

Use `BeliefLedger.open()` as the decision service and `ReferenceRunner` as the minimal owned-boundary
skeleton. Register a `ToolDescriptor`, explicit effect classification, matching versioned policy,
and private handler before `start()`. Ingest through `EvidenceObservation`/`ToolResult`; never grant
trusted provenance or approval from a model-facing operation.

Run:

```bash
uv run --no-sync python -m pytest tests/conformance
uv run --no-sync python -m pytest tests/adapters/reference tests/adapters/hermes tests/gateway tests/mcp
```

Add adapter-specific tests for correlation, repeated callbacks, approval field availability,
unknown tools, handler crashes, token replay, stream cancellation, and competing output paths.
