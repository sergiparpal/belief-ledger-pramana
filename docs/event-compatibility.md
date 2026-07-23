# Event and projection compatibility

Existing envelope schema version 1 hashes are frozen. Their hash material is the previous ASCII
hash, a zero byte, and canonical JSON of every envelope field except `event_hash`, using sorted
keys, compact separators, UTF-8, UTC microsecond timestamps, and no NaN values. Existing v1 payloads
are not modified.

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
