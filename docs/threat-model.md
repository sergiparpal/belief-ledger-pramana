# Threat model

## Decision-service and MCP bypass

A JSONL client can receive a decision and execute an action without the gateway. JSONL therefore
reports `observe`. Only an in-process dispatcher with a private registry can report
`action_enforce`. Likewise, connecting directly to an MCP upstream bypasses wrapped-tool policy;
that warning must remain adjacent to MCP setup. MCP does not own final model-output delivery.

Protected invariants are traceable factual support, structural defeat/retraction, bounded
model-assisted work, conservative tool classification, exact action authorization, append-only
audit history, and non-retention of common credential forms.

Untrusted document/web text remains data. Typing it as testimony is not a prompt-injection defense.
Adapters and hosts remain responsible for instruction/channel isolation, OS/container boundaries,
provider authentication, network policy, and real-world tool authority. Python adapters are trusted
in-process code; installation is a supply-chain/code-trust decision.

## Self-claim scope

A belief the user asserts about themselves is admitted under the `user_self` trust profile rather
than `user_world`. The difference is a verification waiver, not a scalar adjustment: at HIGH stakes
`user_self` is svataḥ and admits on the claim's own authority with `k=0`, while `user_world` is
parataḥ and requires one cross-source confirmation. Content that reached that profile without
coming from the user would be able to admit itself uncorroborated by writing in the first person.

Two independent guards keep the profile bound to the user's own channel. `is_user_self_claim`
refuses any source whose kind is not `USER` before consulting the pattern, and `trust_profile`
independently gates the `user_self` branch on the same kind. Removing either alone still fails a
test. Tool results, fetched pages, files, and replayed prior-ledger beliefs therefore cannot reach
the waiver, whatever they contain.

Within the user channel the pattern is deliberately shallow, and it is not a defense against a user
who lies. It has no negation handling — "I am not the administrator" matches exactly as "I am the
administrator" does — it covers English and Spanish only, and it is satisfied by anything the user
pastes, including text quoted from elsewhere. `tests/unit/test_self_claim_scope.py` characterises
each of those as current behaviour so a change becomes visible in the test file. The waiver is
scoped to who is speaking, not to whether what they say is true.

Production action tokens are cryptographically random, short-lived, and bound to episode/turn,
namespace/name, canonical arguments, target, policy/config content digests, stakes, supports, and
any exact approval receipt. Only SHA-256 token digests persist. Serialized transactions, unique
digests, immutable bindings, terminal state triggers, consume-time revalidation, and support
revocation prevent replay and substitution. Token theft inside the trusted process and an external
effect that lies about its result remain outside this SQLite boundary.

`ResponseGate` prevents provisional HIGH/CRITICAL bytes from reaching the reference adapter's owned
sink. It fails closed for overflow, invalid order/UTF-8, cancellation, linter errors, and sink
preparation failure. This is an in-process at-most-one delivery attempt, not durable exactly-once
messaging. Hermes offers accepted-final transformation only: provisional streaming or competing
transformers can remain visible, so Hermes does not claim strict buffered delivery.

Hash chaining plus a private HMAC key detects database mutation by an attacker who cannot also read
or replace the key. It is not a remote signature or witness and cannot protect against an attacker
who controls the plugin, profile key, and database together.

## External anchoring

The key sits beside the database, so the ability to read the ledger and the ability to forge it are
close to the same ability. An attacker holding the key can edit an event and re-chain everything
after it, and `db verify-chain` then passes: the chain is internally consistent again, and nothing
inside a file the attacker rewrote can say otherwise.

`hermes belief-ledger anchor publish` writes the chain root at a height to an append-only JSONL
sink whose path must resolve outside the ledger directory. `anchor verify` recomputes the local
root at every anchored height; a root that disagrees, or an anchored height the local chain no
longer reaches, is tamper evidence and exits non-zero naming the height and both roots.
`db verify-chain --against-anchors` fails if either check fails, because a passing chain with a
failing anchor is exactly what re-chaining produces.

What this defends against: silent local modification followed by re-chaining.

What it does not defend against: an attacker who controls both the ledger and the sink. A file sink
on the same host raises the cost of tampering by turning one access into two; it is not a barrier,
not a remote witness, and not a timestamping authority. Anchoring is off by default, and a ledger
with no published anchors verifies vacuously — an empty report proves nothing.

Operational requirement: the sink must be backed up and access-controlled independently of the
ledger. In the same backup set, under the same credentials, it is decorative.

Other controls include parameterized SQL, strict schemas, bounded
inputs/graphs/events/context/model calls, private atomic writes, structured and pattern-based
secret redaction before persistence, and no provider credential overrides.
