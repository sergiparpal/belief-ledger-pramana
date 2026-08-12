# 0013 — The chain root is anchored outside the ledger

- **Status:** accepted, 2026-08-10
- **Constrains** [the threat model](../threat-model.md).

## Context

Hash chaining plus a private HMAC key detects mutation by an attacker who cannot read or replace
the key. The threat model already said so. What it did not say is what happens when the attacker
*can*: with the key in hand, an event row can be edited and every event after it re-chained, and
`db verify-chain` passes on the result, because the chain is internally consistent again.

Nothing inside the database can notice that, and the reason is not a missing check. It is that the
integrity key sits beside the database — same directory, same backup set, same access — so the
capability to read the ledger and the capability to forge it are one capability. A check that lives
in the same file as the thing it checks is a check the attacker rewrote.

## Decision

Publish the chain root, at a height, to a sink outside the ledger. Q2 of the plan selected option
A: a local append-only file. No HTTP adapter is built, because none was configured, and an untested
network adapter would be a claim rather than a control.

### The record

`{ledger_id, scope, chain_height, root_hash, hash_algorithm, created_at, package_version,
record_version}`. `scope` is `global` for now. Digests and metadata only — a test asserts the
integrity key appears nowhere in a published record, in raw or hex form.

### The root computation is the existing one

`store.chain_state(up_to_height=...)` and `verify_hash_chain` share `_verified_heads`, which is the
streaming per-episode verification that already existed. There is deliberately no second root
computation: two of them could disagree, and then an anchor mismatch would be ambiguous between
tampering and a bug — which is the one thing this control cannot afford to be.

`up_to_height` is what makes an old anchor checkable after more events have been appended.

### The port and the sink

`ChainAnchorPort` is `publish(record) -> receipt` and `fetch(since_height) -> Iterable[record]`.
`FileAnchorSink` opens `O_APPEND` with mode `0600`, never truncates, never rewrites, and validates
that its path resolves **outside** the ledger directory. That validation is the substance of the
whole design rather than tidiness: a sink inside the ledger directory is not an external anchor,
because whoever rewrote the database is already standing in the directory holding the evidence
against them.

### The commands

```
hermes belief-ledger anchor publish [--scope global]
hermes belief-ledger anchor verify [--since-height N] [--json]
hermes belief-ledger db verify-chain --against-anchors
```

`anchor verify` recomputes the local root at every anchored height and reports three outcomes:
`match`, `mismatch` (the chain at that height is not the chain that was anchored) and `unreachable`
(the local chain never reaches an anchored height, so anchored history is missing). Both failures
are tamper evidence, and they are distinguished because rewriting history and deleting it are
different attacks. Either exits non-zero, naming the height and both roots.

`db verify-chain --against-anchors` exits non-zero if either check fails. A passing chain with a
failing anchor is still a failure — that combination is precisely what a re-chaining attacker
produces.

## What this does not do

Stated here as well as in the threat model, because an overclaimed control is worse than no
control:

- It does not prevent tampering. It makes a particular tamper — local modification followed by
  re-chaining — leave evidence.
- It does not defend against an attacker who controls both the ledger and the sink. A file sink on
  the same host is a cost increase, not a barrier: it turns one access into two.
- It is not a remote signature, a witness, or a timestamping authority.
- An empty or missing sink proves nothing. Anchoring is opt-in and disabled by default
  (`anchoring.sink_path: ""`), and a ledger with no anchors verifies vacuously.

The sink must be backed up and access-controlled independently of the ledger. If both live in the
same backup set, the control is decorative.

## Consequences

- `anchoring.sink_path` is a new configuration key, validated in both validators, defaulting to
  empty. Anchoring off by default means no behaviour changes for an existing deployment.
- `verify_hash_chain`'s implementation moved into `_verified_heads`; its signature and return value
  are unchanged.
- The acceptance test builds a real chain, publishes an anchor, drops the append-only triggers,
  edits an event and faithfully re-chains everything after it, then asserts that `verify_hash_chain`
  still passes and anchor verification fails at the anchored height. Dropping the triggers is not
  cheating: a trigger is a row in the schema of a file the attacker can write.
