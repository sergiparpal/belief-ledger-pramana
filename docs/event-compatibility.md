# Event and projection compatibility

## Envelope

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

`event_hash = SHA256(previous_hash || NUL || canonical_event_without_hash)`, over canonical JSON
that is UTF-8, sorted by key, compact, rejects NaN, and serializes aware datetimes in UTC. Heads
are per episode even though `seq` is database-global.

A separate `event_auth` table stores an
`HMAC-SHA-256(event_id || NUL || event_hash)` tag made with a random, private, profile-local
256-bit key. Core, gateway, MCP, and reference state roots use `.ledger.integrity.key`; the
backward-compatible Hermes profile uses `locks/ledger.integrity.key`. The `auth_tag` shown above is
the hydrated event/export representation; it is stored separately so it is not part of the
hash-chain body. The tag is verified before replay, so rewriting a database and recomputing plain
SHA-256 hashes is rejected unless the attacker can also read and replace the key. This remains
local integrity protection, not a remote signature, witness, or availability guarantee. The key
is secret backup material, not export data, and must be restored with its matching database.

Stable event families cover episode lifecycle and persisted capability/profile selection,
evidence/redaction, source registration/stat
updates, belief admission/status, justification/support, defeat activity, verification,
conflict, retraction, context rendering, component verdict/model usage, lint, gate, approval,
and accepted response accounting. A separate enforcement chain records versioned approval and
action-decision issue/deny/reject/consume/expire/revoke events. It contains token digests and exact
non-secret bindings, never raw tokens.

## Frozen hashes and versioning

Existing envelope schema version 1 hashes are frozen, over the material described above.
Existing v1 payloads are not modified.

New event families use envelope schema version 2 and declare a positive
`payload_schema_version` inside the payload. Payload schema changes require a new payload version;
envelope changes require a new envelope version. Unknown versions are retained for audit but must
not be projected by code that cannot interpret them.

`projection_hash_v1` is SHA-256 of canonical JSON over the exact ordered table/column manifest
`PROJECTION_MANIFEST_V1`. Rows are mapped to those columns and sorted by canonical JSON. Runtime
schema discovery is deliberately excluded. `projection_hash_v2` uses
`PROJECTION_MANIFEST_V2`; adding an empty v2-only table changes v2 by definition and never v1.
V2 currently adds `approval_receipts` and `action_decisions`. Replay reconstructs them from the
append-only enforcement chain while independently comparing v1 and v2 hashes.
Operator output and fixture manifests report the algorithm name and version with each expected
hash.

`LLM_CALL_ATTRIBUTION` is a record kind added after the v1 fixtures were frozen. It is written
alongside `LLM_USAGE_RECORDED` and `COMPONENT_VERDICT_RECORDED` rather than as new fields on either,
because both of those appear in `tests/fixtures/v1_replay/` and a required field added to a frozen
record would move a frozen hash. A record kind absent from every v1 fixture is hash-neutral with
respect to them by construction. It has no projection table, so it changes neither
`projection_hash_v1` nor `projection_hash_v2`; `hermes belief-ledger llm-divergence` reads it from
the event log directly. See [ADR 0012](adr/0012-llm-call-attribution.md).
