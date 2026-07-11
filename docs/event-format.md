# Event format

Every event contains:

```json
{
  "seq": 42,
  "id": "ev_...",
  "episode_id": "ep_...",
  "timestamp": "2026-07-11T10:00:00.000000Z",
  "kind": "BELIEF_STATUS_CHANGED",
  "schema_version": 1,
  "aggregate_type": "belief",
  "aggregate_id": "b_...",
  "correlation": {"turn_id": "..."},
  "causal_event_id": null,
  "payload": {"from": "in", "to": "out", "cause": "rebut:b_..."},
  "previous_hash": "...",
  "event_hash": "..."
}
```

Canonical JSON is UTF-8, sorted by key, compact, rejects NaN, and serializes aware datetimes in
UTC. `event_hash = SHA256(previous_hash || NUL || canonical_event_without_hash)`. Heads are per
episode even though `seq` is database-global. This detects accidental/local mutation; it is not
a signature or remote attestation.

Stable event families cover episode lifecycle, evidence/redaction, source registration/stat
updates, belief admission/status, justification/support, defeat activity, verification,
conflict, retraction, context rendering, component verdict/model usage, lint, gate, approval,
and accepted response accounting.

