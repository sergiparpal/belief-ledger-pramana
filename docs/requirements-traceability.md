# Requirements traceability

This matrix maps the authoritative specification and audited Hermes plan to executable code and
at least one automated check. “Evaluation” means the frozen offline synthetic fixtures; it is not
a claim of external scientific validity.

## Non-negotiable rules

| Requirement | Implementation | Automated evidence |
|---|---|---|
| R1 discrete state and structural defeat | `models.py`, `engine/defeat.py`, status transition events | `tests/unit/test_engine.py`, suite B |
| R2 wrapper/content separation | `ingestion/adapters.py`, `runtime.py:ingest_tool_result` | `tests/integration/test_episode_flow.py::test_wrapper_content_separation_and_lazy_promotion` |
| R3 yogyatā for absence | `ingestion/absence.py`, `engine/validity.py`, `runtime.py:_absence_drafts` | `tests/unit/test_validity.py`, negative-search integration test |
| R4 memory is transport | `engine/trust.py` prior-ledger LIVE rule, memory adapter provenance | `test_live_memory_transport_reentry_requires_reobservation` and trust tests |
| R5 monitor is content | `models.ComponentVerdict`, `llm/client.py`, `_component_verdict_drafts` | extractor/linter/NLI/auditor component-inference tests |
| R6 independent testimony | `ingestion/provenance.py`, `verification/scheduler.py` | provenance unit/evaluation fixtures |
| R7 qualifiers precede contradiction | `engine/qualifiers.py`, `engine/contradiction.py` | `tests/unit/test_qualifiers_contradiction.py` |
| R8 append-only event sourcing | `events.py`, `store.py`, immutable SQL triggers | store immutability/hash/replay tests |

## Specification sections

| Spec area | Implementation | Automated evidence |
|---|---|---|
| §0 scope/non-goals | README and threat model; no execution/anti-injection/probability/memory subsystem | docs/package review |
| §1 principles | modules listed in R1–R8 | rule rows above |
| §2.1 `Source`, stats, all enums | `models.py` | construction/serialization/store fixtures |
| §2.1 evidence and `EvidenceRef` | `models.py`, evidence projections | store and ingestion tests |
| §2.1 `Justification`, `Belief`, `DefeatEdge`, `VerificationTask` | `models.py`, projections | engine, query/explain tests |
| §2.2 atomic/self-contained/qualified/bounded content | `engine/validity.py`, claim validator | validity and malformed claim tests |
| §2.2 exact/near dedup and corroboration roots | `ingestion/provenance.py`, `_candidate_drafts` | idempotency/provenance scenarios |
| §2.3 SQLite baseline tables | `migrations.py` plus operational tables | fresh DB/migration/replay tests |
| §3 PRATYAKṢA validity/defeaters | validity registry, ingestion support/UNDERCUT | validity and engine tests |
| §3 ŚABDA validity/defeaters | span validation, āpta source, REBUT/UNDERCUT | hash-only and wrapper/content tests |
| §3 ANUMĀNA validity/defeaters | model tool, graph cycle check, live premises/warrant | inference and propagation tests |
| §3 ARTHĀPATTI validity | explanandum/alternatives validation | tool schema/validity tests |
| §3 UPAMĀNA validity | similarity-basis validation and lowest default rank | tool schema/priority tests |
| §3 ANUPALABDHI/yogyatā | absence assessment and positive fixed rule | absence and engine tests |
| §3 user/model/prior-ledger rules | trust profiles and adapter source kinds | complete trust-matrix test |
| §4.1 REBUT/UNDERCUT | `models.DefeatKind`, `engine/defeat.py` | engine winner/undercut scenarios |
| §4.2 lexicographic priority/fixed rules | `engine/priority.py`, packaged ranks | suite B, priority tests |
| §4.2 saṃśaya | equal/cycle PENDING plus persistent conflict/task | equal-priority test/integration |
| §4.3 fixed point/termination/reinstatement | `engine/defeat.py` state detection/ceiling | generated finite chains and reinstatement test |
| §4.4 structural retraction | rendered-belief projection, notice/descendants/ack | stale-claim end-to-end test |
| §5.1 svataḥ/parataḥ/quarantine | `engine/trust.py` | every matrix cell test |
| §5.2 complete source × stakes matrix | `data/defaults.yaml` | 32-cell parametrized test |
| §5.3 cross-source/tool/chain/human verification | `verification/*`, ordinary tool suggestions | scheduler/chain tests |
| §5.3 budgets/honest PENDING | `llm/client.py`, config limits | budget and fallback tests |
| §5.4 āpta learning | `engine/trust.effective_competence`, `verification/apta.py`, source-stat events | defeat source-stat assertions |
| §6.1 relevance/mandatory order/graph expansion | `context/select.py`, `context/render.py` | bounded context snapshots/integration |
| §6.2 typed grammar/conflicts/retractions/contract | `context/render.py` | Unicode/ASCII and retraction-order tests |
| §6.3 typed compression constraint | depth/count/character selection preserves IDs/types | bounded/property context tests |
| §7.1 tool ingestion | adapters, evidence privacy, lazy inbox | wrapper/content and idempotency tests |
| §7.2 user/model ingestion | user claims and inference model tool | integration/tool contract tests |
| §7.3 vikalpa linter | `lint/*`, component verdict events | linter unit tests and suite D |
| §7.4 action gate | `gate/*`, audited approval directive | gate tests and suite C |
| §8.1 five middleware protocol points | `hermes/hooks.py`, `hermes/middleware.py` | contract/integration tests |
| §8.2 turn ordering/non-accumulative context | runtime hooks and request injector | ephemeral middleware test |
| §9 stale-blog trace | integrated user→inference→new observation→retraction→ack flow | `test_stale_claim_retracts_descendant_and_reinstates_context` |
| §10 suite A grounding | `evaluations/suite_a_grounding`, report runner | evaluation e2e test |
| §10 suite B bādha | `evaluations/suite_b_badha` | wrong-winner/propagation gates |
| §10 suite C failures/actions | `evaluations/suite_c_agent_failures` | unsafe/false-block gates |
| §10 suite D monitor | `evaluations/suite_d_linter` | precision/recall gates |
| §10 ablations/overhead/collapse | `evaluations/ablations.py`, frozen config/report | evaluation e2e test/report |
| §11 roadmap | staged `IMPLEMENTATION_STATE.md` and verification script | complete local gate |
| §12 risks | README, threat model, evaluation metrics | suites/fuzz/property checks |
| Appendix A trairūpya/hetvābhāsa | `verification/chain_audit.py`, schema/prompt | chain-audit validation tests |
| Appendix B concept mapping | matching domain models/modules | this matrix plus architecture docs |

## Added operational entities

| Plan entity | Implementation/projection | Evidence |
|---|---|---|
| `Episode` | model, `episodes`, ordered resolver/runtime budgets | episode/contract tests |
| canonical `Event` | model, event builder/hash chain | event/store tests |
| `IngestionSupport` | model/table/UNDERCUT support | engine tests |
| `Conflict` | model/table/equal-priority task | conflict tests |
| `RetractionNotice` | model/table/TTL/ack | end-to-end retraction test |
| `RenderedBelief` | model/table/context event | retraction trigger test |
| `ComponentVerdict` | model/table/R5 ANUMĀNA when premises exist | extraction/lint flows |
| `LlmUsage` | model/table/per-purpose attribution/budget projection | scripted/failure model tests |

## Hermes technical contract

| Plan §5 requirement | Implementation | Contract evidence |
|---|---|---|
| root manifest and relative directory shim | `plugin.yaml`, root `__init__.py` | directory generated-namespace test |
| module entry point | `pyproject.toml` | entry-point metadata test and smoke script |
| Python/Hermes range, no provider keys | package metadata/compatibility | contract checker |
| cheap deterministic registration | `plugin.register` only creates adapters/closures | registration test (no state created) |
| JSON tool envelopes/validation/`**kwargs` | `hermes/tools.py`, strict schemas | every-tool contract test |
| hook aliases/correlation | `hermes/hooks.py`, runtime resolver | `args`/`arguments` and session tests |
| per-request middleware | `hermes/middleware.py` | pinned source checker/integration test |
| four API payload shapes | `context/inject.py` | parametrized shape snapshots |
| no system/tool/auth/cache mutation | injector edits only last user content | immutability assertions |
| `ctx.llm` active routing/bounds | `llm/client.py` no override kwargs | scripted call assertions |
| session/subagent/approval lifecycle | hook adapters/events | callback contract tests |
| slash and operator CLI | `hermes/slash_commands.py`, `hermes/cli.py` | parser/command tests |
| doctor/competition/enablement diagnostics | compatibility and CLI doctor | doctor tests |
| directory/private state permissions | config/runtime | POSIX permission tests |
| HIGH/CRITICAL outer safety boundaries | hook gate/output catches | failure-injection tests |
