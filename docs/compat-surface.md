# Backward-compatible import surface

`belief_ledger_pramana` is a 1.x compatibility contract, recorded in
[ADR 0007](adr/0007-host-neutral-product-surface.md). New integrations should import from
`belief_ledger_core` — [the Python API](python-api.md) says so explicitly — but the Pramana import
package must keep working for existing Hermes installations.

This file is the answer to Stage 0 question **R3** of the obvious-fix plan and the input to Stage 7.
It records what is importable today, and separates what is *promised* from what is merely *reachable*.

## What is promised

Three sources make an actual promise. Nothing else does.

| Source | Promise |
|---|---|
| `belief_ledger_pramana.__all__` | `Pramana`, `Stakes`, `Status`, `__version__` |
| `plugin.yaml` | the entry point `belief_ledger_pramana.plugin`, its four `pramana_*` tools, and thirteen hooks |
| `pyproject.toml` `[project.entry-points."hermes_agent.plugins"]` | `belief-ledger-pramana = "belief_ledger_pramana.plugin"` |

`docs/python-api.md` states that adapter internals are not a supported import path.
`scripts/check_product_claims.py` pins the Hermes contract commit in prose; it pins no Python symbol.
`tests/core/test_public_api.py` exercises `belief_ledger_core` alone and asserts nothing about this
package.

That is the whole of the promise. Everything in the next section is reachable because the modules
exist, not because anything committed to keeping it.

## Rule for Stage 7

A module-level `__all__` inside this package is an *intent* marker, not a promise — but it is the
only intent marker available, so Stage 7 treats it as the working definition of the surface and
holds it fixed while modules move underneath it. Concretely:

1. Every name below stays importable from the module path shown, or the change is a break and needs
   the deprecation path in Stage 7c.
2. A module with no `__all__` promises only that the module imports. Its contents are free to move.
3. `belief_ledger_pramana.engine.defeat` exports `_defeat_cycle_nodes`, an underscore-prefixed name.
   It is listed below because it is in `__all__` today, but it is a private name in a public
   `__all__` and Stage 7 should not treat it as load-bearing.

## Full reachable surface

Generated from `pkgutil.walk_packages` over the installed package at commit
`3d21ccddce66a9659f3509cc1b5758a025541c52`. Regenerate rather than hand-edit.

- `belief_ledger_pramana` — `Pramana`, `Stakes`, `Status`, `__version__`
- `belief_ledger_pramana.admission` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.application` — `ActionEvaluationUseCase`, `ContextCompilationUseCase`, `LedgerQueryService`, `LifecycleEventRecorder`
- `belief_ledger_pramana.application.actions` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.application.context` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.application.lifecycle` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.application.queries` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.application.verification` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.atomic` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.compatibility` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.config` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.context` — `ContextInjectionError`, `HermesRequestInjector`, `RenderedContext`, `render_context`
- `belief_ledger_pramana.context.budget` — `CharacterBudget`
- `belief_ledger_pramana.context.inject` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.context.render` — `RenderedContext`, `render_belief_line`, `render_context`
- `belief_ledger_pramana.context.select` — `Selection`, `lexical_score`, `select_beliefs`
- `belief_ledger_pramana.contracts` — `ApprovalResult`, `EnforcementProfile`, `EpisodeContext`, `HostCapabilities`, `NormalizedDecision`, `OutputCandidate`, `ProfileSelection`, `ToolInvocation`, `ToolResult`, `negotiate_profile`
- `belief_ledger_pramana.core_config` — `CoreConfigSnapshot`, `FrozenDict`, `FrozenList`, `freeze_config`, `load_core_config`
- `belief_ledger_pramana.core_runtime` — `CapabilityShortfall`, `LedgerRuntime`, `RuntimeEvent`
- `belief_ledger_pramana.data` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.dependencies` — `CallableStructuredModel`, `ClockPort`, `FakeStructuredModel`, `FixedClock`, `FixedMonotonicClock`, `IdentityPort`, `MonotonicClockPort`, `RuntimeDependencies`, `SecureIdentity`, `SecureToken`, `SequenceIdentity`, `SequenceToken`, `StructuredModelBudgetError`, `StructuredModelError`, `StructuredModelPort`, `StructuredModelProviderError`, `StructuredModelRequest`, `StructuredModelResult`, `StructuredModelTimeout`, `StructuredModelValidationError`, `SystemClock`, `SystemMonotonicClock`, `TokenPort`, `deterministic_dependencies`
- `belief_ledger_pramana.engine` — `RelabelResult`, `ValidityResult`, `relabel`, `validate_belief`
- `belief_ledger_pramana.engine.contradiction` — `ContradictionDecision`, `candidate_pair`, `candidate_tokens`, `classify_deterministically`
- `belief_ledger_pramana.engine.defeat` — `RelabelResult`, `_defeat_cycle_nodes`, `relabel`
- `belief_ledger_pramana.engine.graph` — `adjacency`, `cycle_path`, `descendants`
- `belief_ledger_pramana.engine.priority` — `PriorityComparison`, `PriorityTrace`, `compare_priority`, `priority_trace`
- `belief_ledger_pramana.engine.qualifiers` — `SUPPORTED_QUALIFIERS`, `ScopeReconciliation`, `canonicalize_qualifiers`, `reconcile_qualifiers`, `units_compatible`
- `belief_ledger_pramana.engine.retractions` — `affected_subgraph`, `notice_expired`
- `belief_ledger_pramana.engine.trust` — `TrustDecision`, `determine_admission`, `effective_competence`, `trust_profile`
- `belief_ledger_pramana.engine.validity` — `ValidityResult`, `normalize_content`, `validate_belief`, `validate_content`
- `belief_ledger_pramana.errors` — `HashChainError`, `LlmReservationError`, `StoreError`
- `belief_ledger_pramana.events` — `EventDraft`, `build_event`, `canonical_json`, `compute_event_auth`, `compute_event_hash`, `content_hash`, `isoformat_utc`, `parse_datetime`, `to_primitive`, `utc_now`
- `belief_ledger_pramana.gate` — `ActionGate`
- `belief_ledger_pramana.gate.classify` — `ActionClassification`, `ActionPolicy`, `ActionPolicyRegistry`
- `belief_ledger_pramana.gate.decision` — `ActionGate`, `ActionGateReader`, `ActionGateWriter`, `arguments_digest`
- `belief_ledger_pramana.gate.preconditions` — `PreconditionResult`, `resolve_preconditions`
- `belief_ledger_pramana.hermes` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.hermes.cli` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.hermes.hooks` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.hermes.middleware` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.hermes.model_port` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.hermes.schemas` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.hermes.slash_commands` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.hermes.tools` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.ids` — `is_typed_id`, `new_id`, `require_id`
- `belief_ledger_pramana.infrastructure` — `SqliteEventWriter`, `SqliteLedgerMaintenance`, `SqliteLedgerReader`, `SqliteLlmBudgetLedger`
- `belief_ledger_pramana.infrastructure.sqlite_ledger` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.ingestion` — `PreparedEvidence`, `prepare_evidence`
- `belief_ledger_pramana.ingestion.absence` — `AbsenceAssessment`, `assess_negative_search`
- `belief_ledger_pramana.ingestion.adapters` — `AdaptedToolResult`, `SourceDescriptor`, `ToolAdapterRegistry`
- `belief_ledger_pramana.ingestion.claims` — `ClaimCandidate`, `ClaimValidation`, `candidate_from_structured`, `deterministic_candidates`, `validate_candidate`
- `belief_ledger_pramana.ingestion.provenance` — `fingerprint`, `independent`, `normalize_url`, `provenance_root`, `registrable_domain`, `similarity`
- `belief_ledger_pramana.ingestion.tool` — `PreparedEvidence`, `prepare_evidence`, `redact_secrets`, `redacted_content_hash`
- `belief_ledger_pramana.ingestion.user` — `is_about_user_self`, `user_source`
- `belief_ledger_pramana.integrity` — `IntegrityKeyError`, `load_or_create_integrity_key`
- `belief_ledger_pramana.lint` — `enforce_report`, `lint_response`
- `belief_ledger_pramana.lint.enforce` — `enforce_report`, `linter_failure_response`
- `belief_ledger_pramana.lint.extract` — `ExtractedClaim`, `extract_claims`, `strip_citations`
- `belief_ledger_pramana.lint.match` — `deterministic_entailment`, `match_claim`
- `belief_ledger_pramana.lint.report` — `lint_response`
- `belief_ledger_pramana.llm` — `HostLlmClient`, `LlmBudgetError`, `StructuredCallResult`
- `belief_ledger_pramana.llm.client` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.llm.prompts` — `CHAIN_AUDIT`, `CLAIM_EXTRACTION`, `CONTRADICTION`, `LINT_ENTAILMENT`, `REWRITE`
- `belief_ledger_pramana.llm.schemas` — `CHAIN_AUDIT_SCHEMA`, `CLAIM_EXTRACTION_SCHEMA`, `CONTRADICTION_SCHEMA`, `LINT_ENTAILMENT_SCHEMA`, `REWRITE_SCHEMA`
- `belief_ledger_pramana.migrations` — `LATEST_SCHEMA_VERSION`, `MigrationResult`, `PROJECTION_HASH_ALGORITHM`, `PROJECTION_HASH_V1_ALGORITHM_VERSION`, `PROJECTION_HASH_V2_ALGORITHM_VERSION`, `PROJECTION_MANIFEST_V1`, `PROJECTION_MANIFEST_V2`, `PROJECTION_TABLES`, `SCHEMA_V1`, `SCHEMA_V2`, `SCHEMA_V3`, `SCHEMA_V4`, `SCHEMA_V5`, `SCHEMA_V6`, `SCHEMA_V7`, `configure_connection`, `migrate`
- `belief_ledger_pramana.models` — `Belief`, `ChainAudit`, `CompatibilityMode`, `ComponentVerdict`, `Conflict`, `DefeatEdge`, `DefeatKind`, `Episode`, `Event`, `Evidence`, `EvidenceRef`, `GateDecision`, `GateOutcome`, `Health`, `IngestionSupport`, `Integrity`, `Justification`, `LintClaim`, `LintDisposition`, `LintReport`, `LlmUsage`, `Perishability`, `Pramana`, `RenderedBelief`, `RetractionNotice`, `STAKE_RANK`, `Source`, `SourceKind`, `SourceStats`, `Stakes`, `Status`, `VerificationMethod`, `VerificationTask`, `max_stakes`
- `belief_ledger_pramana.plugin` — `register`
- `belief_ledger_pramana.policy_cli` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.ports` — `ActionGateLedger`, `ActionGateReader`, `ContextReader`, `EpisodeLifecycleStore`, `EventWriter`, `HostLlmFacade`, `LedgerQueryReader`, `LlmBudgetLedger`, `VerificationLedger`, `VerificationTaskReader`
- `belief_ledger_pramana.projections` — `ProjectionHandler`, `apply_event`
- `belief_ledger_pramana.runtime` — no `__all__`; no symbol promised beyond the module itself
- `belief_ledger_pramana.store` — `EventDraft`, `LedgerStore`, `LlmReservationError`, `PurgeResult`, `ReplayResult`, `StoreError`, `ZERO_HASH`
- `belief_ledger_pramana.verification` — `VerificationScheduler`
- `belief_ledger_pramana.verification.apta` — `updated_source`
- `belief_ledger_pramana.verification.chain_audit` — `local_asiddha`, `validate_chain_audit`
- `belief_ledger_pramana.verification.methods` — `method_instruction`
- `belief_ledger_pramana.verification.scheduler` — `VerificationResult`, `VerificationScheduler`

Seventy-seven modules, every one of which imported without error.
