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

Use `ReferenceRunner` as the minimal skeleton and run:

```bash
uv run python -m pytest tests/conformance
uv run python -m pytest tests/adapters/reference tests/adapters/hermes
```

Add adapter-specific tests for correlation, repeated callbacks, approval field availability,
unknown tools, handler crashes, token replay, stream cancellation, and competing output paths.
