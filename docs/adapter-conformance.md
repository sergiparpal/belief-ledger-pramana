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
