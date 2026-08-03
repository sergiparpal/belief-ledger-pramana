# Gateway JSONL protocol

`belief-ledger serve --transport jsonl --state-root PATH` is a local, serialized, UTF-8
newline-delimited JSON decision service. It reports `observe`: the client can bypass the process and
execute an action, so an `allow` response is not enforced execution.

## Envelope

One request and one response occupy one line. Lines are bounded to 1,048,576 encoded bytes. Empty
lines are ignored; malformed JSON and invalid UTF-8 produce errors. Keys are serialized in sorted
order with compact separators for deterministic output.

The limit is enforced while reading, not after. The reader never buffers more than the limit for a
single line: once a line passes it, further content is discarded rather than accumulated. A line
that exceeds the limit is answered with one `LINE_TOO_LARGE` error and its remainder is drained up
to the next newline, so the discarded tail is never interpreted as further requests. The stream
resynchronizes on the following line and continues.

```json
{"schema_version":1,"request_id":"r1","idempotency_key":"start-1","operation":"episode.start","context":{"session_id":"s","turn_id":"t","task_id":"task","platform":"local","model":"caller"}}
{"schema_version":1,"request_id":"r1","ok":true,"result":{"schema_version":1,"id":"episode_0001","state":"active"}}
```

`schema_version` must be `1`. `request_id` is reflected. The state root is selected by the process
command, not by individual requests. A stream owns one episode and serializes its calls.

An optional non-empty idempotency key returns the cached response for the same request; reuse for
different content fails with `IDEMPOTENCY_KEY_REUSED`. `request_id` is excluded from that
comparison, so a retry may correlate itself with a fresh `request_id` and still be served the
original response — including the original `request_id`, since the cached response is returned
verbatim. Every other field is compared.

`evidence.ingest` additionally passes the key to the ledger's durable idempotency layer as
`gateway:<key>`, scoped to the episode. The in-memory cache is bounded and does not survive a
restart; the durable layer is what prevents a replay from ingesting the same evidence twice after
eviction or restart. A `gateway:`-prefixed key cannot collide with one a caller supplies in
`observation.correlation`.

Errors use:

```json
{"schema_version":1,"request_id":"","ok":false,"error":{"reason_code":"MALFORMED_JSON","line":2,"detail":"line is not valid JSON"}}
```

## Operations

| Operation | Required fields | Result |
|---|---|---|
| `capabilities` | none | `observe`, protocol/line limits, no execution ownership |
| `episode.start` | `context` | episode handle; required before stateful calls |
| `episode.finalize` | none | finalized handle |
| `evidence.ingest` | normalized `observation` | evidence/belief admission identifiers |
| `action.evaluate` | `invocation` with name/namespace/arguments | decision identifiers/digests, never a raw permit |
| `decision.explain` | `decision_id` | policy, supports, conflicts, validity, transitions |
| `output.evaluate` | `content`, `stakes`, optional `final` | evaluated content, never a delivery claim |
| `ledger.verify-chain` | none | hash-chain and projection result |
| `ledger.replay` | none | deterministic replay result |
| `shutdown` | none | clean response and stop after the current line |

Clean EOF shuts down without an error. Stateful operations before `episode.start`, unsupported
operations, oversized lines, invalid fields, and schemas fail with stable reason codes. Responses
never include raw action permits, credentials, authorization headers, or integrity keys.

Python callers that need an enforced local action use `GatewayDispatcher`. It keeps registered
handlers private, consumes atomically before handler lookup, and reports at most `action_enforce`.
