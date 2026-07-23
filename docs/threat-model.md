# Threat model

Protected invariants are traceable factual support, structural defeat/retraction, bounded
model-assisted work, conservative tool classification, exact action authorization, append-only
audit history, and non-retention of common credential forms.

Untrusted document/web text remains data. Typing it as testimony is not a prompt-injection defense.
Adapters and hosts remain responsible for instruction/channel isolation, OS/container boundaries,
provider authentication, network policy, and real-world tool authority. Python adapters are trusted
in-process code; installation is a supply-chain/code-trust decision.

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
who controls the plugin, profile key, and database together. Other controls include parameterized
SQL, strict schemas, bounded inputs/graphs/events/context/model calls, private atomic writes,
structured and pattern-based secret redaction before persistence, and no provider credential
overrides.
