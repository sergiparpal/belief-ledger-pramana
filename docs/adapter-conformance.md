# Adapter conformance specification

This contract is host-neutral. An adapter normalizes host identifiers into `EpisodeContext`, starts
an episode once, reports audited `HostCapabilities`, ingests evidence and approvals, classifies every
tool from a complete inventory or declares inventory unavailable, and finalizes the episode after
all accepted events are durable.

## Required ordering and failure semantics

1. Start the episode and persist the requested/effective profile plus the capability snapshot.
2. Ingest the user turn before compiling and injecting request context.
3. Normalize a tool invocation and apply an explicit read-only/effectful policy.
4. For an effectful strict dispatch, issue an exact decision and consume its opaque token in a
   serialized transaction immediately before the handler. A missing, expired, altered, revoked, or
   used token blocks execution. A crash after consumption does not restore the token.
5. Ingest tool results with the same episode, stable turn, namespace, name, and call correlation.
6. Buffer HIGH/CRITICAL output, lint the complete UTF-8 candidate, then deliver either the accepted
   bytes once or the deterministic block report. No provisional byte may reach the sink.
7. Finalize, verify the event chain, and ensure projections replay deterministically.

Repeated lifecycle notifications must be idempotent. Unknown identifiers are normalized visibly,
not invented from unrelated host state. Adapter-specific diagnostics are allowed, but domain event,
decision, and projection meanings are shared. Unsupported strict assertions require stable missing-
capability reasons; they are never silently skipped.

The gateway JSONL adapter is decision-only and demonstrates `observe`; its in-process dispatcher
demonstrates `action_enforce`. MCP inspection demonstrates `observe`; the complete-inventory proxy
demonstrates at most `action_enforce` and requires direct-upstream access to be excluded by the
deployment. `ReferenceRunner` is the strict construction: it starts empty and conformance fixtures
must inject unrelated tool domains to prove that no deployment assumptions are built in.

## Authoring an adapter

Implement the normalized values and ordering above without importing another adapter. Declare only
capabilities proved by audited host behavior.

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
