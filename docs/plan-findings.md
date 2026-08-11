# Obvious-fix plan — findings register

Append-only. Entries are never edited or removed once written; a superseded entry gets a later
entry that supersedes it, and the original stays. Opened at Stage 0 of
`belief-ledger-pramana-obvious-fixes-plan.md` and carried to
[the completion report](plan-completion-report.md).

An entry belongs here whenever any of the following happens: a bug is found outside the plan's
scope, an existing test has to change, a coupling blocks a pure move, a limitation is deliberately
left in place, or the work starts to look like design rather than implementation.

---

### F-01 — Documented schema version is prose, not a checkable constant

- **Stage:** 0
- **Severity:** minor
- **What:** `LATEST_SCHEMA_VERSION = 7` lives at
  `packages/core/src/belief_ledger_core/migrations.py:438`. Three documents state it in three
  different renderings: `docs/upgrade-and-rollback.md:34` says "The current schema is 7",
  `docs/architecture.md:47` says "Schema v7", `docs/operations.md:48` says "Schema v7". None uses a
  form a naive string check would find from the constant name.
- **Why not fixed here:** it is not a defect — it is the constraint Stage 1 has to design around.
  Recorded so the Stage 1 checker's per-fact rendering set is traceable to an observation rather
  than to an assumption.
- **Suggested next step:** Stage 1 gives each fact a set of accepted renderings and requires at
  least one match per listed document.

### F-02 — Three schema versions have no migration SQL file

- **Stage:** 0
- **Severity:** minor
- **What:** `belief_ledger_pramana/data/migrations/` and
  `packages/core/src/belief_ledger_core/data/migrations/` each contain `0001_initial.sql`,
  `0002_llm_reservations.sql`, `0003_performance_indexes.sql` and `0006_enforcement.sql`. Versions
  4, 5 and 7 exist only as the in-code constants `SCHEMA_V4`, `SCHEMA_V5` and `SCHEMA_V7` in
  `packages/core/src/belief_ledger_core/migrations.py`. Nothing currently asserts that every version
  in `1..LATEST_SCHEMA_VERSION` is covered by one or the other, so a genuinely missing version would
  look identical to this deliberate split.
- **Why not fixed here:** the split is intentional and correct — those three versions have no DDL —
  so the fix is a check, not a file. It is the sixth fact in Stage 1's table.
- **Suggested next step:** Stage 1 asserts coverage of every version by SQL file *or* code
  constant, which distinguishes the deliberate case from the accidental one.

### F-03 — `belief_ledger_pramana` had no automated import-surface pin before Stage 7a

- **Stage:** 0
- **Severity:** significant
- **What:** `tests/core/test_public_api.py` imports only from `belief_ledger_core`. No test in the
  repository asserts that any symbol remains importable from `belief_ledger_pramana`, even though
  ADR 0007 makes that package a 1.x compatibility contract and `plugin.yaml` names it as the
  Hermes entry point. Seventy-seven modules are reachable; four names are promised.
- **Why not fixed here:** it is Stage 7a's entire deliverable, and Stage 7a is ordered last by
  design.
- **Suggested next step:** land Stage 7a's snapshot test before any move in 7b–7d, exactly as the
  plan sequences it.

### F-04 — `_defeat_cycle_nodes` is a private name inside a public `__all__`

- **Stage:** 0
- **Severity:** minor
- **What:** `belief_ledger_pramana/engine/defeat.py` re-exports `_defeat_cycle_nodes` through its
  `__all__`, so an underscore-prefixed name is advertised as part of the module's intended surface.
- **Why not fixed here:** removing it from `__all__` is a surface change, and the surface has no
  pin yet (F-03). Doing it before Stage 7a would be changing an unmeasured thing.
- **Suggested next step:** Stage 7a records it in the snapshot as present-but-private; Stage 7b
  decides whether to drop it from `__all__` under the deprecation path.

### F-05 — Two of the three documents named for the schema fact never stated it at all

- **Stage:** 1
- **Severity:** minor
- **What:** the plan's Stage 1 table requires `LATEST_SCHEMA_VERSION` to appear in
  `docs/upgrade-and-rollback.md`, `docs/operations.md` and `docs/architecture.md`. On first run the
  new checker reported that only the first stated it; the other two discussed schema v6 and v7 by
  name but never said which version is current. `README.md` likewise never stated the
  `requires-python` range that the plan requires of it — only `HERMES_COMPATIBILITY.md` did.
- **Why not fixed here:** it *was* fixed here. The entry exists because the failure mode is worth
  recording: this is absence, not staleness, and a checker that only compared stated values would
  have passed all three files silently. That is why a listed document matching the pattern nowhere
  is a failure rather than a skip.
- **Suggested next step:** none; keep the "states it nowhere" branch of the checker, and keep
  `tests/unit/test_doc_invariants.py::test_a_document_that_drops_the_fact_entirely_fails` pinning
  it.

### F-06 — The imprecise defeat claim was in the specification, not the README

- **Stage:** 2
- **Severity:** minor
- **What:** the plan attributes "scalar confidence never decides defeat" to `README.md`. A search of
  the whole tree finds the claim only at `docs/belief-ledger-pramana-spec-v0.1.md:15`; `README.md`
  makes no claim about defeat scalars at all, and §4.2 of the same specification already listed
  `reliability` as the third key correctly.
- **Why not fixed here:** nothing to fix in `README.md`. Recorded so the completion report is not
  read as having silently skipped a file the plan named.
- **Suggested next step:** none.

### F-07 — For SHABDA, competence also determines `type_rank`, not only `reliability_rank`

- **Stage:** 2
- **Severity:** significant
- **What:** `_type_key` in `packages/core/src/belief_ledger_core/engine/priority.py` bands testimony
  into `shabda_apta_hi`/`_mid`/`_lo` by `effective_competence`, using the packaged thresholds 0.8
  and 0.5. The same scalar that is the third key therefore also moves the second one. A competence
  gap crossing a band boundary is decided at `type_rank` and never reaches `reliability_rank`. This
  was found by writing the Stage 2 pinning test, which failed on its first run: a 0.9-vs-0.2
  contest between two SHABDA beliefs resolved at `type`, not at `reliability`.
- **Why not fixed here:** it is not obviously a defect. Banding testimony by source competence is a
  deliberate modelling choice, and changing it would alter defeat outcomes — design work, not
  implementation, and therefore out of scope by §0.
- **Suggested next step:** decide whether the band coupling is intended to be load-bearing or is an
  accident of expressing "how good is this witness" twice; if the former, say so in the
  specification's §4.2 rather than only in the code. Note that it compounds the §2.3
  `effective_competence` feedback loop, which is already out of scope.

### F-08 — The self-claim privilege is a verification waiver, not the competence bump the plan describes

- **Stage:** 2b
- **Severity:** significant
- **What:** the plan describes `is_about_user_self` as raising source competence "from `0.65` to
  `0.95`". That is not the mechanism. `about_self` reaches `trust_profile` in
  `packages/core/src/belief_ledger_core/engine/trust.py:41`, which selects `user_self` over
  `user_world`. In the packaged trust matrix those differ at HIGH stakes: `user_self` is `svatah`
  with `k=0`, `user_world` is `paratah` with `k=1` and a `cross_source` method. The privilege is
  therefore a waiver of cross-source verification, which is a larger grant than a scalar bump and
  is invisible in `effective_competence`.
- **Why not fixed here:** nothing to fix — the mechanism is correct, only the description was
  wrong. The scope guard the plan asks for is the right control either way, and is now in place.
- **Suggested next step:** none for the guard. Anyone reasoning about this privilege should read
  the trust matrix rather than the competence dictionary.

### F-09 — `user_source` advertises a `self: 0.95` competence that nothing can reach

- **Stage:** 2b
- **Severity:** minor
- **What:** `user_source` in `packages/core/src/belief_ledger_core/ingestion/user.py` returns
  `competence={"self": 0.95, "general": 0.65}`, and `source-profiles.yaml` carries the same pair for
  the `user` profile. `effective_competence` keys that dictionary by `belief.domain`, and the only
  extractor on the user path — `deterministic_candidates` — always emits `domain="general"`. No
  code path assigns `domain="self"`, so the 0.95 entry is unreachable configuration.
- **Why not fixed here:** two defensible fixes exist — delete the dead entry, or make the user path
  set `domain="self"` when the claim is a self-claim — and the second changes admission outcomes.
  Choosing between them is design work, and §0 puts that out of scope.
- **Suggested next step:** decide whether self-claims are meant to carry a domain of their own. If
  they are not, delete the entry from both the code and `source-profiles.yaml` so it stops implying
  a mechanism that does not exist.
  `tests/unit/test_self_claim_scope.py::test_the_self_competence_entry_is_unreached_by_the_user_ingestion_path`
  fails the day something starts reaching it.

### F-10 — The self-claim pattern has no negation handling and covers two languages

- **Stage:** 2b
- **Severity:** significant
- **What:** `_SELF` in `packages/core/src/belief_ledger_core/ingestion/user.py` matches "I am not
  the administrator" exactly as it matches "I am the administrator", covers English and Spanish
  only — German "Ich bin ..." and French "Je suis ..." do not match — and is satisfied by any text
  on the user channel, including text the user pasted from elsewhere.
- **Why not fixed here:** explicitly out of scope for Stage 2b, which covers the mechanical scope
  guard only. Negation handling and language coverage are modelling decisions.
- **Suggested next step:** the eleven cases in
  `tests/unit/test_self_claim_scope.py::test_self_pattern_characterisation` assert current
  behaviour with each limitation named at its assertion, so a change to the pattern shows up there
  first. Treat that parametrization as the specification when the pattern is revisited.

### F-11 — No existing test or evaluation suite covered stale-versus-fresh defeat

- **Stage:** 3
- **Severity:** significant
- **What:** making `recency_rank` unconditional changes which belief wins whenever two otherwise
  identical beliefs differ only in age. Running the full suite before adding new tests produced one
  failure, and it was the naive-datetime construction test — not a single defeat outcome moved
  across `tests/`, `evaluations/` suites A–E, or the integration tests. The saṃśaya-versus-recency
  case the change exists to fix was not covered anywhere.
- **Why not fixed here:** it *is* covered now, by `tests/unit/test_recency_priority.py`. The entry
  records the gap itself, which is a data point for the plan's out-of-scope §1.1: 43 hand-written
  cases with perfect scores did not exercise a decision the engine makes on every relabel.
- **Suggested next step:** when evaluation methodology is revisited, treat "which existing case
  would fail if this rule were inverted?" as the admission criterion for a suite case. A rule no
  case can distinguish is a rule the suite does not test.

### F-12 — An existing test changed: naive timestamps are now refused at construction

- **Stage:** 3
- **Severity:** minor
- **What:** `tests/unit/test_domain_edges.py::test_graph_retractions_apta_methods_and_ids`
  previously constructed a `Belief` with `datetime(2026, 7, 11)` and asserted
  `pytest.raises(ValueError, match="timezone-aware")` around
  `priority_trace(naive, source, packaged_yaml("defaults.yaml"))`. The construction now raises, so
  `priority_trace` is never reached and the original assertion could not run.
- **Why not fixed here:** the assertion was moved, not weakened. It now wraps the `Belief(...)`
  call, and a second assertion was added that an aware belief yields a non-zero `recency_rank`.
  The new form asserts strictly more: the invalid value cannot be constructed at all, rather than
  being caught later by one consumer. The old expectation — that a naive timestamp is rejected —
  still holds and is still tested; only the boundary at which it is rejected moved, which is what
  ADR 0011 decided.
- **Suggested next step:** none.

### F-13 — The plan's `STATIC` perishability is spelled `STABLE` in code

- **Stage:** 3
- **Severity:** minor
- **What:** the plan's §6.4 asks for a test with `STATIC` perishability. `Perishability` in
  `packages/core/src/belief_ledger_core/models.py` has `STABLE`, `SLOW`, `FAST`, `LIVE`. There is
  no `STATIC`.
- **Why not fixed here:** a naming difference, not a defect. The tests parametrize over `STABLE`,
  which is the class the plan meant.
- **Suggested next step:** none.

### F-14 — `llm/prompts.py` calls itself versioned but carries no version

- **Stage:** 4
- **Severity:** minor
- **What:** the module docstring of `packages/core/src/belief_ledger_core/llm/prompts.py` reads
  "Versioned concise instructions for fallible model components", but the module holds five bare
  string constants and no version. The schema names carry a `_v1` suffix and the JSON schemas carry
  a `$id` ending `.v1`; those version the *schemas*, not the prompts.
- **Why not fixed here:** the plan says to reuse the existing version rather than invent a parallel
  one, and there is none to reuse. Adding a `PROMPT_VERSION` constant would create a second thing
  to keep in step, and a prompt edit that forgot to bump it would silently group two different
  prompts as one.
- **Suggested next step:** none required. `prompt_hash` in `llm/attribution.py` identifies each
  prompt by digesting its own text, which cannot drift from the text. If an explicit version is
  ever wanted for human-readable reporting, derive it from the digest rather than maintaining both.

### F-15 — An existing test changed: a successful model call now emits three events

- **Stage:** 4
- **Severity:** minor
- **What:**
  `tests/core/test_core_services.py::test_core_structured_model_client_records_success_and_stable_failure`
  asserted `len(result.event_ids) == 2`, covering `LLM_USAGE_RECORDED` and
  `COMPONENT_VERDICT_RECORDED`. `LLM_CALL_ATTRIBUTION` makes it three.
- **Why not fixed here:** the assertion was replaced with a stronger one rather than a bumped
  number. It now asserts the three event kinds by name and in order, so a future change that swaps
  one record for another cannot keep the test green the way a count could. The old expectation —
  that a successful call records usage and a verdict — still holds and is still asserted.
- **Suggested next step:** none.

### F-16 — Sampling was neither host-controlled nor configurable: it was a hardcoded literal, twice

- **Stage:** 4
- **Severity:** significant
- **What:** the plan's Part A branches on whether the port can accept sampling parameters, and
  neither branch matched. `StructuredModelRequest` had no sampling field, but
  `belief_ledger_pramana/hermes/model_port.py` and `belief_ledger_pramana/llm/client.py` were each
  passing `temperature=0.0` to the Hermes facade as a literal. Sampling was controlled, in two
  places, invisibly, with nothing tying the two together or recording what was applied.
- **Why not fixed here:** it *was* fixed. `SamplingPolicy` is now a validated, configurable value
  carried on the request and recorded on every attribution event, and both call sites read it from
  one helper. The entry exists because the failure mode is instructive: a value that is correct but
  unexpressed reads, from outside, exactly like a value that is out of our control.
- **Suggested next step:** none. Note that `temperature=0.0` still cannot make a host
  deterministic, which is why Part B exists and why `docs/operations.md` says an empty divergence
  report is not a proof of determinism.

### F-17 — `verification/chain_audit.py` is about inference chains, not the hash chain

- **Stage:** 5
- **Severity:** minor
- **What:** the plan says "`chain_audit.py` already computes chain state. Do not write a second root
  computation; extract and reuse the existing one." That module is about *anumāna* chain audits —
  `local_asiddha` and `validate_chain_audit` check warrants and premise statuses. It computes no
  hash-chain state at all. The name collides.
- **Why not fixed here:** the instruction's intent was followed against the code that actually
  computes chain state, `LedgerStore.verify_hash_chain`. Its head computation was extracted into
  `_verified_heads`, and `chain_state` reuses it, so there is still exactly one root computation.
  `anchors.py` was placed next to `chain_audit.py` as the plan directs.
- **Suggested next step:** consider renaming `verification/chain_audit.py` to something naming
  inference — the collision is a live trap for exactly this kind of change. Not done here because a
  rename touches the public compat surface and Stage 7a's pin does not exist yet.

### F-18 — Append-only triggers are not a control against an attacker who can write the file

- **Stage:** 5
- **Severity:** minor
- **What:** `events_no_update` and `events_no_delete` abort any `UPDATE` or `DELETE` on `events`.
  Writing the tamper-simulation test made it concrete that a trigger is a row in the schema of a
  file the attacker can write: `DROP TRIGGER` and the protection is gone. The test does exactly
  that, then restores them.
- **Why not fixed here:** there is nothing to fix. The triggers defend against a buggy or careless
  caller inside the process, which is a real and different threat, and they do that well. The entry
  exists so nobody reads them as tamper resistance.
- **Suggested next step:** none. The threat model's anchoring section now states what does and does
  not detect an attacker with file access.

### F-19 — An existing test hardcoded the schema numbers in the migration dry run

- **Stage:** 6
- **Severity:** minor
- **What:**
  `tests/integration/test_operator_surfaces.py::test_operator_cli_and_slash_command_cover_normal_workflow`
  asserted `{"current_schema": 7, "target_schema": 7, ...}` against `db migrate --dry-run`. Bumping
  `LATEST_SCHEMA_VERSION` to 8 broke it.
- **Why not fixed here:** it was fixed by removing the coupling rather than by bumping the numbers.
  Both fields now assert against `LATEST_SCHEMA_VERSION`, which is what the command derives them
  from — the CLI already had a comment saying the target must never be a literal, and the test was
  the literal.
- **Suggested next step:** none.

### F-20 — A migration test silently stopped exercising its migration when a version was added

- **Stage:** 6
- **Severity:** significant
- **What:**
  `tests/unit/test_audit_regressions.py::test_legacy_unscoped_idempotency_rows_migrate_and_replay_cleanly`
  reproduced the pre-v7 on-disk shape and then rolled the schema stamp back with
  `DELETE FROM schema_migrations WHERE version = LATEST_SCHEMA_VERSION`. That only re-ran the v7
  rescoping migration for as long as v7 *was* `LATEST_SCHEMA_VERSION`. Adding schema 8 made the
  rollback delete only the v8 stamp, so the migration loop restarted at v8, v7 never re-ran, and
  the test failed against an unscoped key.
- **Why not fixed here:** it *was* fixed, and the entry records that this was a latent defect in
  the test rather than a consequence of adding schema 8 — schema 8 only revealed it. Had the test
  asserted something weaker it would have kept passing while testing nothing. The rollback now
  targets the rescoping migration by number (`WHERE version >= 7`), which is what the test's own
  comment always said it was doing.
- **Suggested next step:** when a test rolls back schema state to force a migration to re-run, name
  the migration it is exercising. `LATEST_SCHEMA_VERSION` is never the right handle for that,
  because the test is about one specific migration and not about whichever one happens to be last.

### F-21 — A corrupt snapshot payload raised instead of being discarded

- **Stage:** 6
- **Severity:** minor
- **What:** `load_newest_valid` verified each payload's content hash, but `zlib.decompress` runs
  before the hash can be computed, so a truncated payload raised `zlib.error` out of
  `replay(from_snapshot=True)` — from a code path that had a correct full-replay fallback available
  and should have taken it. Found by `test_a_corrupted_payload_is_discarded_rather_than_used`, which
  was written expecting the discard behaviour the invariant promises.
- **Why not fixed here:** it was fixed. Decompression and JSON decoding are now guarded and any
  failure discards the snapshot.
- **Suggested next step:** none. This is invariant 2 doing its job: anything unusable about a
  snapshot must degrade to a full replay, never to an error.

### F-22 — `LedgerRuntime` is a fixture, so its callers cannot be mechanically migrated

- **Stage:** 7c
- **Severity:** minor
- **What:** the plan says to migrate every internal caller of the facade to the core API.
  `LedgerRuntime` is not a wrapper over `BeliefLedger`: `ingest_health` and `authorize_deployment`
  in `packages/core/src/belief_ledger_core/runtime.py` encode the deployment-gate fixture's own
  policy — a hardcoded `deploy-production` rule, a `sha256:fixture-policy-v1` revision, and a
  green/red health toggle. `BeliefLedger` has no equivalent, which
  `docs/python-api.md` already hinted at by calling it a "deprecated fixture facade".
- **Why not fixed here:** migrating `examples/deployment_gate/run.py` off it means rewriting the
  example against a different policy model. That is design work, out of scope by §0. The three
  remaining callers are the deterministic example and two test modules that exercise the facade on
  purpose, which is what the plan says the end state should be.
- **Suggested next step:** treat the facade's removal in 2.0.0 as a task to rewrite the
  deployment-gate example against `BeliefLedger` with an explicit manifest, not as a deletion.
  `tests/unit/test_core_runtime.py::test_the_facade_is_a_fixture_and_not_a_core_api_wrapper` pins
  the asymmetry so this does not have to be rediscovered.

### F-23 — `EpisodeService` cannot be split by a pure move

- **Stage:** 7d
- **Severity:** significant
- **What:** after `runtime.py` was split into a package, `episode_service.py` is 2,430 lines and
  holds exactly one class. Reaching the 600-line limit would require distributing `EpisodeService`
  across modules via mixins or by relocating methods onto collaborators. Both change the class
  rather than move it, so Stage 7d's hard rule — revert the move, leave the cluster, record it —
  applies. The same blocker applies to `store.py` (1,601, one class), `api.py` (1,178, the public
  core API) and `enforcement.py` (1,097).
- **Why not fixed here:** a class decomposition is design work with real behavioural risk, and the
  plan puts design out of scope. Doing it inside a refactor commit would also make the diff
  unreviewable, which is the reason the pure-move rule exists.
- **Suggested next step:** the cohesive seams inside `EpisodeService` are visible in the method
  order and would survive extraction as collaborators rather than mixins: evidence ingestion and
  claim promotion (`ingest_user_message`, `ingest_tool_result`, `_prepare_tool_evidence`,
  `_promote_evidence`, `_claim_admission_drafts`, `_candidate_drafts`); relabel orchestration
  (`relabel`, `_edge_activity_drafts`, `_belief_transition_drafts`, `_conflict_transition_drafts`,
  `_relabel_summary_drafts`); output lint (`lint_and_enforce`, `_semantic_lint`, `pre_verify`,
  `record_accepted_response`); and verification (`request_verification`, `complete_verification`,
  `run_chain_audit`, `_verification_drafts`). Each takes the store and config it already uses; the
  question a design pass has to answer is what state genuinely has to stay shared.

### F-24 — Re-export shims were kept rather than deleted

- **Stage:** 7b
- **Severity:** minor
- **What:** the plan's 7b would delete shim modules "not in the promised surface". Only four names
  are formally promised by `belief_ledger_pramana.__all__`, so applying that rule literally would
  authorize deleting almost the entire package — 77 modules that existing Hermes installations
  import by path.
- **Why not fixed here:** deliberate. Deleting them is a breaking change to a 1.x compatibility
  contract (ADR 0007) with no measured need behind it, and ground rule 1.1.7 requires a deprecation
  path for any such removal. `docs/compat-surface.md` records what is promised versus what is
  merely reachable, so a future pass can make that call with the distinction already drawn.
- **Suggested next step:** if the shims are ever removed, do it at 2.0.0 alongside the
  `LedgerRuntime` removal, in one breaking release with one migration note, rather than
  piecemeal.
