# Threat model

Protected invariants are traceable factual support, structural defeat/retraction, bounded
model-assisted work, pre-effect action checks, append-only audit history, and non-retention of
common credential forms.

Untrusted document/web text remains data. Typing it as UNTRUSTED testimony does not make it safe
to execute and is not a prompt-injection defense. Hermes and the surrounding harness remain
responsible for instruction/channel isolation, OS/container boundaries, dangerous-command
approval, provider authentication, and network policy.

The plugin itself is trusted in-process code. A malicious plugin can access Hermes privileges;
plugin installation is therefore a supply-chain/code-trust decision. Hash chaining detects
history mutation but provides no secret key, signature, remote witness, or protection against an
attacker who can replace both database and plugin.

Controls include parameterized SQL, strict structured schemas plus local validation, input/graph/
event/context/model-call bounds, request-bound authenticated internal context markers, path-local
atomic writes, secret-pattern redaction, a strict non-compositional terminal read grammar,
conservative unknown action classification, no provider overrides, and HIGH/CRITICAL fail-closed
callback boundaries. Generic execution stdout is not treated as a factual source; only typed
adapters may promote content claims.
