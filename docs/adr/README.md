# Decision records

A record states the context that forced a decision, the decision itself, and what it costs.
Semantic deviations from [the specification](../belief-ledger-pramana-spec-v0.1.md) require one,
and [requirements traceability](../requirements-traceability.md) cites the record next to the code
and the automated check that pins it.

| Record | Status | Decision |
|---|---|---|
| [0001 Hermes integration surface](0001-hermes-integration-surface.md) | accepted, 2026-07-11 | Integrate through audited public hooks plus `llm_request` middleware; never monkey-patch the host. |
| [0002 Event-sourced SQLite](0002-event-sourced-sqlite.md) | accepted, 2026-07-11 | SQLite WAL is the episode store; immutable canonical events and per-episode SHA-256 chains are the source of truth. |
| [0003 Plugin-only enforcement limits](0003-plugin-only-enforcement-limits.md) | accepted, 2026-07-11 | A plugin cannot claim exclusive output control; caught callback exceptions, transform precedence, and provisional streaming bound what may be claimed. |
| [0004 Product positioning](0004-product-positioning.md) | accepted, 2026-07-22 | The approved headline, the three operator outcomes it leads with, and the claims the project must not make. |
| [0005 Host-neutral core](0005-host-neutral-core.md) | accepted, 2026-07-22 | Core owns the domain and may not import a host; adapters normalize host shapes before calling it. |
| [0006 Enforcement capabilities](0006-enforcement-capabilities.md) | accepted, 2026-07-22 | Capability booleans describe audited observable host behaviour; `false` means absent, unknown, or unproven. |
| [0007 Host-neutral product surface](0007-host-neutral-product-surface.md) | accepted | Five distributions with one-way dependencies; only the gateway owns `belief-ledger`, and the Pramana name stays a 1.x compatibility contract. |
| [0008 Permit lifecycle fails closed on finalized episodes](0008-permit-lifecycle-fails-closed-on-finalized-episodes.md) | accepted, 2026-08-03 | Episode lifecycle is re-read inside the authorization transaction, so a permit bound to a finalized episode is refused instead of depending on revocation having run. |
| [0009 Incremental relabeling](0009-incremental-relabeling.md) | proposed, 2026-08-05 | The relabel fixed point stays whole-episode; measurement identifies contradiction detection as the quadratic term, and only it becomes incremental, behind a differential test on emitted events. |
| [0010 Scalar competence in the priority order](0010-scalar-competence-in-the-priority-order.md) | accepted, 2026-08-10 | `reliability_rank` stays the third lexicographic key; the specification is corrected to say so, including that the same scalar bands SHABDA at `type_rank`. Removing it was rejected because every contest it settles would become `PENDING`, which has no drain. |
| [0011 Unconditional recency key](0011-unconditional-recency-key.md) | accepted, 2026-08-10 | `recency_rank` is computed for every perishability class, not only `fast`/`live`, so stale-versus-fresh resolves instead of producing saṃśaya. It stays fifth, which bounds the change by position; timezone awareness moves to `Belief.__post_init__`. |
| [0012 LLM call attribution](0012-llm-call-attribution.md) | accepted, 2026-08-10 | Every model call records prompt, input and output digests plus the applied sampling policy as a new `LLM_CALL_ATTRIBUTION` record, and `llm-divergence` reports identical inputs that produced different outputs. |
| [0013 External chain anchoring](0013-external-chain-anchoring.md) | accepted, 2026-08-10 | The chain root is published to an append-only sink outside the ledger directory, so local modification followed by re-chaining leaves evidence. It raises the cost of tampering; it does not prevent it. |
| [0014 Snapshots as a discardable cache](0014-snapshots-as-a-discardable-cache.md) | accepted, 2026-08-10 | Schema 8 adds a `snapshots` cache that is never the source of truth: any snapshot may be deleted with no loss, a stale derivation fingerprint means discard rather than upgrade, full replay stays the default, and `db verify-snapshot` proves an accelerated rebuild equals a full one. |

0009 is proposed. It records a measurement and a direction; no code has changed for it, and the
per-ingestion behaviour it describes is the behaviour that ships today.
