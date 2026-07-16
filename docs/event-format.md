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
  "event_hash": "...",
  "auth_tag": "..."
}
```

Canonical JSON is UTF-8, sorted by key, compact, rejects NaN, and serializes aware datetimes in
UTC. `event_hash = SHA256(previous_hash || NUL || canonical_event_without_hash)`. Heads are per
episode even though `seq` is database-global. A separate `event_auth` table stores an
`HMAC-SHA-256(event_id || NUL || event_hash)` tag made with a random, private, profile-local
256-bit key at `locks/ledger.integrity.key`. The `auth_tag` shown above is the hydrated
event/export representation; it is stored separately so it is not part of the hash-chain body.
The tag is verified before replay, so rewriting a database and recomputing plain SHA-256 hashes
is rejected unless the attacker can also read and replace the key. This remains local integrity
protection, not a remote signature, witness, or availability guarantee. The key is secret backup
material, not export data, and must be restored with its matching database.

Stable event families cover episode lifecycle, evidence/redaction, source registration/stat
updates, belief admission/status, justification/support, defeat activity, verification,
conflict, retraction, context rendering, component verdict/model usage, lint, gate, approval,
and accepted response accounting.

