# Belief Ledger Pramāṇa for Hermes Agent — autonomous implementation plan

## 1. Document purpose

This is the implementation plan for turning the design in
[`belief-ledger-pramana-spec-v0.1.md`](belief-ledger-pramana-spec-v0.1.md) into a
standalone, installable Hermes Agent plugin. It is written for execution by a
programming agent such as Claude Code CLI. The implementation agent must read
this plan and the complete reference specification before changing code.

The plan is self-contained with respect to the repository structure, Hermes
integration contract, architecture, implementation sequence, test strategy,
and release gates. The reference specification remains authoritative for the
epistemic semantics: the pramāṇa types, rules R1–R8, validity conditions,
defeat semantics, trust matrix, context grammar, linter behavior, evaluation
suites, and the scope/non-goals.

The implementation must proceed from stage to stage without waiting for human
verification. Every stage ends in machine-checkable gates. Brief user input is
permitted only for choices that cannot safely be inferred, such as authorizing
a public release, choosing a license before publication, approving paid live
model evaluations, or resolving a genuinely destructive data migration. None
of those choices may block implementation, local packaging, or offline tests.

## 2. Audited Hermes target and source precedence

### 2.1 Compatibility baseline

Implement and test the first full-conformance release against:

- Hermes Agent version: `0.18.2`
- Audited upstream commit: `3b2ef789dfcf92f5b7b18c08c59d25948e50857f`
- Audit date: `2026-07-11`
- Python range required by that Hermes snapshot: `>=3.11,<3.14`
- Plugin manifest version understood by that snapshot: `1`

The exact commit matters because Hermes develops quickly and its indexed
documentation can lag its source. Full conformance in this plan uses the
`llm_request` middleware present in the audited source so that a freshly
compiled ledger block is injected ephemerally before every provider request,
including requests after tool calls. Do not silently substitute the behavior
of an older release.

Use this precedence when sources differ:

1. The reference specification controls the ledger's epistemic behavior.
2. Contract tests against the pinned Hermes commit control runtime behavior.
3. The pinned Hermes source controls details absent from public docs.
4. Official Hermes documentation controls the documented public surface.
5. Assumptions and examples in this plan come last.

Record any required deviation in an ADR and in the requirements traceability
matrix; never bury a deviation in code comments alone.

### 2.2 Official and pinned references

The implementation agent should not need to browse to execute the plan, but
these references establish where the Hermes requirements came from:

- [Official Hermes plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
- [Official Hermes plugin and discovery overview](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Official Hermes hook reference](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)
- [Official plugin LLM access reference](https://hermes-agent.nousresearch.com/docs/developer-guide/plugin-llm-access)
- [Pinned `pyproject.toml`](https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/pyproject.toml)
- [Pinned plugin manager and `PluginContext`](https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/hermes_cli/plugins.py)
- [Pinned behavior-changing middleware contracts](https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/hermes_cli/middleware.py)
- [Pinned tool-call hook ordering](https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/model_tools.py)
- [Pinned per-provider-request middleware call site](https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/agent/conversation_loop.py)
- [Pinned final-output transform call site](https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/agent/turn_finalizer.py)
- [Pinned plugin trust model](https://github.com/NousResearch/hermes-agent/blob/3b2ef789dfcf92f5b7b18c08c59d25948e50857f/SECURITY.md#25-plugin-trust-model)

## 3. Intended outcome

The finished repository must provide a general Hermes plugin named
`belief-ledger-pramana` that:

1. Creates one episode-scoped ledger for each Hermes session/task.
2. Ingests user claims and every tool result with wrapper/content separation.
3. Maintains append-only evidence, beliefs, justifications, defeat edges,
   verification tasks, source statistics, and status transitions in SQLite.
4. Implements deterministic JTMS-style relabeling, REBUT and UNDERCUT, open
   conflict handling, reinstatement, and propagated retractions.
5. Applies the configured `svataḥ` / `parataḥ` / quarantine policy by source,
   domain, integrity, and effective stakes.
6. Compiles a relevant, token-bounded epistemic block before every Hermes LLM
   provider request without modifying the persistent transcript or system
   prompt.
7. Lints final model output for unsupported factual claims and enforces the
   configured LOW/MED/HIGH/CRITICAL policy as far as Hermes' plugin boundary
   allows.
8. Gates effectful tool calls before execution using ledger-backed
   preconditions, with deterministic block messages and optional Hermes-native
   approval escalation.
9. Provides model tools, slash commands, operator CLI commands, replay/export,
   diagnostics, and an auditable event trail.
10. Runs in CLI, gateway, one-shot/headless, and delegated-agent contexts that
    expose the audited Hermes plugin contract.
11. Ships as both a Git/directory plugin and a Python entry-point package.
12. Includes offline deterministic tests and the evaluation suites described
    in section 10 of the reference specification.

## 4. Scope boundaries and honest plugin-only limitations

Preserve all non-goals in section 0 of the reference specification. In
particular, do not turn the plugin into an anti-prompt-injection product, a
probabilistic reasoner, a general knowledge graph, or a long-term memory
backend.

Also make these Hermes-specific limitations explicit in the README, doctor
output, and compatibility tests:

- Python plugin callbacks run in-process with the same privileges as Hermes.
  Installation is a code-trust decision, not a sandbox boundary.
- Hermes catches callback exceptions. An uncaught policy-hook exception can
  therefore fail open. All policy callbacks must have an outer safety boundary
  that returns an explicit block for HIGH/CRITICAL actions when internal
  evaluation fails.
- `transform_llm_output` can replace the final response but cannot generally
  restart an arbitrary non-coding turn or force more tool calls. On unresolved
  HIGH/CRITICAL grounding failures, the plugin must replace the answer with a
  safe blocked-response report rather than claim automatic resolution.
- `pre_verify` can continue a coding turn, but only when Hermes detected code
  edits and only up to Hermes' bounded nudge count. Treat it as an optimization,
  not the universal output gate.
- Multiple `transform_llm_output` plugins all receive the original output and
  Hermes uses the first non-empty replacement in registration order. `doctor`
  must detect and report competing transformers. Strict enforcement is only
  claimed when the belief-ledger transform has effective precedence or no
  competitor exists.
- Streaming surfaces may have displayed provisional tokens before a final
  transform. Preserve Hermes' normal transformed-response reconciliation and
  document that the plugin's hard guarantee applies to the accepted final
  response, not necessarily every provisional streamed token.
- Hermes tool schemas do not expose a universal `stakes` or `side_effects`
  field. This plugin must maintain a versioned, configurable action-policy
  registry and conservatively classify unknown tools.
- Full per-request ephemeral injection requires the audited
  `ctx.register_middleware("llm_request", ...)` contract. An older hook-only
  Hermes may run a clearly labeled compatibility mode, but it must never report
  full conformance.

Do not monkey-patch Hermes internals to hide these constraints. If an upstream
extension is eventually needed, isolate it behind a capability interface and
open a separate, minimal upstream change; the standalone plugin must remain
usable without a Hermes source fork.

## 5. Hermes Agent technical contract

### 5.1 Plugin form, discovery, and activation

Build a general `kind: standalone` Python plugin. The repository root must be
directly installable by `hermes plugins install`, so it must contain both
`plugin.yaml` and a root `__init__.py` with a callable `register(ctx)`.

Hermes discovery and activation requirements to support:

| Source | Location/mechanism | Requirement |
|---|---|---|
| User directory | `$HERMES_HOME/plugins/belief-ledger-pramana/` | `plugin.yaml`, `__init__.py`, and explicit enablement |
| Project directory | `./.hermes/plugins/belief-ledger-pramana/` | Also requires `HERMES_ENABLE_PROJECT_PLUGINS=1` |
| Pip package | `hermes_agent.plugins` entry-point group | Entry point must load a module exposing `register(ctx)` |
| Git installer | `hermes plugins install OWNER/REPO --enable` | Plugin files must be at repository root, unless users explicitly select a subdirectory |

General/user plugins are opt-in. Automated smoke tests must run
`hermes plugins enable belief-ledger-pramana` before expecting hooks or tools,
and gateway tests must restart/recreate the gateway process after activation.
`plugins.disabled` wins over `plugins.enabled` and must be detected by doctor.
`HERMES_SAFE_MODE=1` skips plugin discovery entirely. Later discovery sources
can replace an earlier plugin with the same key, so doctor must report the
loaded module path/source rather than checking the manifest name alone. Use
`HERMES_PLUGINS_DEBUG=1`, `hermes plugins list`, and Hermes' agent log for
discovery diagnostics.

Use the following initial manifest shape:

```yaml
manifest_version: 1
name: belief-ledger-pramana
version: 0.1.0
kind: standalone
description: Episode-scoped typed belief ledger, defeat engine, context compiler, output linter, and action gate
provides_tools:
  - pramana_record_inference
  - pramana_query
  - pramana_explain
  - pramana_request_verification
provides_hooks:
  - pre_llm_call
  - pre_tool_call
  - transform_tool_result
  - transform_llm_output
  - post_llm_call
  - pre_verify
  - on_session_start
  - on_session_end
  - on_session_finalize
  - on_session_reset
  - subagent_start
  - subagent_stop
  - post_approval_response
```

Add `author` only when known. Do not declare API-key environment variables:
model-assisted components use the host-owned `ctx.llm` facade and the user's
active Hermes credentials. The manifest's `provides_*` lists are descriptive;
`register(ctx)` must still register every actual tool, hook, command, and
middleware.

### 5.2 Packaging contract

Use a conventional package plus a root directory-plugin shim:

```text
root __init__.py
  -> imports `.belief_ledger_pramana.plugin` relatively and calls register(ctx)

pyproject entry point
  [project.entry-points."hermes_agent.plugins"]
  belief-ledger-pramana = "belief_ledger_pramana.plugin"
```

The entry-point value must name the module, not `module:register`, because the
audited Hermes loader calls `ep.load()` and then looks up `register` on the
loaded object.

Use package-relative imports (`from .runtime import ...`, etc.) throughout the
implementation. Hermes loads a directory plugin under a generated
`hermes_plugins.*` namespace, so assuming `belief_ledger_pramana` is a top-level
import works for the wheel but can fail for the directory plugin. Load packaged
data through `importlib.resources`, not paths relative to the process working
directory.

Set `requires-python = ">=3.11,<3.14"`. For the first release, constrain the
pip host dependency to `hermes-agent>=0.18.2,<0.19`; widen only after contract
CI passes against the new series. Keep directory-plugin runtime imports to the
standard library plus dependencies shipped by Hermes (notably PyYAML). Hermes'
Git plugin installer clones code but does not generally install arbitrary
third-party runtime dependencies. Any future optional heavy dependency belongs
in this package's pip extras; a third-party plugin cannot add itself to Hermes'
in-tree lazy-dependency allowlist.

Put `pytest`, `pytest-cov`, `pytest-asyncio`, `hypothesis`, `ruff`, `mypy`,
`build`, and `twine` in a development extra. Keep test/evaluation libraries out
of the runtime dependency set.

Hermes calls `register(ctx)` once when loading the plugin and disables a plugin
whose registration raises while continuing the agent. Keep registration cheap
and deterministic: perform capability checks and registrations, but no paid
LLM call, database replay, network access, or long-running worker startup.
Lazily initialize episode services at the first relevant hook/command.

### 5.3 Tool handler contract

Every model-visible handler must:

- Have the shape `def handler(args: dict, **kwargs) -> str` unless explicitly
  registered as async.
- Accept unknown `**kwargs` for forward compatibility.
- Validate all arguments; schemas must use specific descriptions, types,
  required fields, enums, bounds, and `additionalProperties: false` where
  compatible.
- Always return a JSON string on success and failure.
- Catch expected and unexpected exceptions and serialize a stable error object;
  never let an exception escape into Hermes' tool loop.
- Never permit the model to forge PRATYAKṢA or ŚABDA. Model-authored records are
  restricted to ANUMĀNA, ARTHĀPATTI, or UPAMĀNA with valid premises/warrants.
- Preserve Hermes task/session/correlation kwargs when resolving the episode.

Use one response envelope:

```json
{"ok": true, "data": {}, "warnings": [], "event_ids": []}
```

or:

```json
{"ok": false, "error": {"code": "stable_code", "message": "actionable text"}}
```

### 5.4 Hook and middleware contract

All callbacks receive keyword arguments and must accept `**kwargs`. Register
these adapters:

| Hermes extension point | Plugin responsibility | Behavior-changing return |
|---|---|---|
| `pre_llm_call` | Resolve/create episode, bind `turn_id` when present, ingest the original user message, set the turn query/stakes | In full mode return `None`; in compatibility mode return `{"context": compiled_block}` |
| `llm_request` middleware | Compile the current ledger before every provider request and append it ephemerally to the active user message in the copied request | `{"request": modified_request, "source": "belief-ledger-pramana", "reason": "epistemic-context"}` |
| `pre_tool_call` | Classify effective stakes, evaluate ledger-backed preconditions, record the decision | `None`, `{"action":"block","message":"..."}`, or audited-current `{"action":"approve",...}` |
| `transform_tool_result` | Ingest the post-truncation/post-redaction tool result before it reaches the next LLM request | Return `None` so the original tool result remains intact |
| `transform_llm_output` | Lint, record verdicts, and replace output when policy requires annotation/rewrite/block | Non-empty replacement string, otherwise `None` |
| `pre_verify` | For coding turns only, run a one-shot grounding check before Hermes accepts a stop | At most once, `{"action":"continue","message":"..."}` |
| `post_llm_call` | Record the accepted/transformed assistant response and finish turn accounting | Ignored |
| Session hooks | Initialize, checkpoint, rotate, finalize, and clean in-memory handles | Ignored |
| Subagent hooks | Record parent/child provenance and treat child summaries as model testimony | Ignored |
| `post_approval_response` | Record the user's runtime confirmation/denial as a source event | Ignored |

Normalize callback aliases at the Hermes boundary instead of leaking them into
domain code. In particular, accept both audited-source `args` and documented
`arguments` for transformed tool results, and map approval `session_key` into
the episode resolver without assuming it is identical to every other Hermes
session field. Missing optional correlation fields must be tolerated and
recorded as missing, not synthesized from unrelated values.

The audited middleware kinds are `tool_request`, `tool_execution`,
`llm_request`, and `llm_execution`. Only `llm_request` is required for the
initial architecture. Do not wrap tool execution when the documented
`pre_tool_call` plus `transform_tool_result` sequence is sufficient.

Hermes appends `pre_llm_call` context to the current user message, not the
system prompt, and normally spills hook outputs over 10,000 characters. Keep
the plugin's compiled context at or below 8,000 characters by default so the
hook-only fallback never spills. Request middleware is also explicitly
bounded to the same plugin limit even though it is not governed by the hook
spill setting.

### 5.5 Per-request provider-shape support

The `llm_request` middleware receives already-built provider kwargs. Implement
an idempotent `HermesRequestInjector` with tests for all audited API modes:

- `chat_completions`: edit the last `role: user` message; append to string
  content or add a `{"type":"text","text":...}` block.
- `anthropic_messages`: append a `type: text` block to the last user message,
  including when that message also contains `tool_result` blocks; never break
  role alternation.
- `bedrock_converse`: append a `{"text": ...}` content block to the last user
  message.
- `codex_responses`: edit the last user input item and append a
  `{"type":"input_text","text":...}` block.

Never change `system`, `instructions`, tool schemas, tool-call/result
adjacency, provider credentials, or cache-routing fields. Work on the
middleware's copied request. A missing/unknown message shape produces a
`CONTEXT_INJECTION_FAILED` event and activates degraded-mode policy; it must
not guess at a provider payload.

### 5.6 Host-owned LLM access

Use `ctx.llm.complete_structured(...)` for claim extraction, contradiction
classification, chain audit, and semantic linting. Use:

- `temperature=0.0`;
- strict, versioned JSON schemas;
- bounded `max_tokens` and explicit `timeout`;
- a distinct `purpose`, such as `belief-ledger.claim-extraction`;
- the active provider/model by default;
- local validation of `result.parsed` even when Hermes performed schema
  validation;
- event records for provider/model, token usage, purpose, latency, and outcome,
  but never credentials.

Do not pass `provider=`, `model=`, `agent_id=`, or `profile=` by default. Hermes
denies those overrides unless the operator grants the corresponding
`plugins.entries.belief-ledger-pramana.llm.*` trust settings. Optional auxiliary
model routing must remain opt-in and must degrade to the active model or a
deterministic fallback.

## 6. Hermes-adapted runtime flow

The full-mode turn flow is:

```text
user message
  -> pre_llm_call
       resolve episode + ingest user evidence/claims + bind turn query
  -> every LLM provider request
       llm_request middleware -> compile current ledger -> ephemeral injection
  -> model requests a tool
       pre_tool_call -> ALLOW / APPROVE / BLOCK
       if allowed: Hermes executes tool
       transform_tool_result -> immutable evidence + beliefs + relabel
  -> next provider request sees the newly compiled ledger
  -> model produces final text
       pre_verify (coding turns only, optional one-shot continuation)
       transform_llm_output -> lint + safe final replacement if needed
       post_llm_call -> persist accepted response + accounting
  -> on_session_end -> checkpoint only (this hook fires after every turn)
  -> on_session_finalize/reset -> flush and release the outgoing episode
```

Important adaptation decisions:

1. `pre_llm_call` performs user ingestion but does not carry the full context
   in full mode. This avoids duplicate injection.
2. `llm_request` recompiles on every actual model request, which realizes the
   reference specification's non-accumulative, per-call context contract.
3. `transform_tool_result` changes ledger state and returns `None`; the raw
   Hermes result remains the transcript's tool result. The following request
   middleware pass supplies the updated ledger ephemerally.
4. `on_session_end` is not the end of a multi-turn session in Hermes. Use it
   for a WAL checkpoint/metrics flush, not destructive cleanup.
5. Session creation and state access are lazy and idempotent because hook order
   can vary across CLI, gateway, headless, and test runners.

## 7. Repository layout

Create this target layout. Modules may be combined only when doing so preserves
the named interfaces and test boundaries.

```text
belief-ledger-pramana/
├── plugin.yaml
├── __init__.py                       # directory-plugin register shim
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── after-install.md
├── config.example.yaml
├── CLAUDE.md                         # autonomous execution/testing rules
├── HERMES_COMPATIBILITY.md
├── IMPLEMENTATION_STATE.md           # machine-updated phase/evidence log
├── belief_ledger_pramana/
│   ├── __init__.py
│   ├── plugin.py                     # register(ctx), feature gates
│   ├── compatibility.py              # version/capability checks
│   ├── runtime.py                    # episode registry and service container
│   ├── config.py                     # load/merge/validate config
│   ├── models.py                     # enums and immutable domain records
│   ├── ids.py                        # collision-safe IDs
│   ├── events.py                     # event definitions/canonical encoding
│   ├── store.py                      # SQLite transactions and event append
│   ├── projections.py                # materialize/replay views
│   ├── migrations.py
│   ├── engine/
│   │   ├── graph.py                  # justification DAG and descendants
│   │   ├── validity.py               # per-pramāṇa admission checks
│   │   ├── qualifiers.py             # canonical scope/time reconciliation
│   │   ├── priority.py               # configured lexicographic ordering
│   │   ├── contradiction.py          # candidate blocking + adjudication
│   │   ├── defeat.py                 # REBUT/UNDERCUT and fixed point
│   │   ├── retractions.py
│   │   └── trust.py                  # svataḥ/parataḥ/quarantine + āpta
│   ├── ingestion/
│   │   ├── user.py
│   │   ├── tool.py
│   │   ├── adapters.py               # per-tool source/provenance adapters
│   │   ├── claims.py                 # lazy structured extraction
│   │   ├── provenance.py             # roots, dedup, independence
│   │   └── absence.py                # yogyatā checks / SEARCH_FAILED
│   ├── verification/
│   │   ├── scheduler.py
│   │   ├── methods.py
│   │   ├── chain_audit.py
│   │   └── apta.py
│   ├── context/
│   │   ├── select.py
│   │   ├── render.py
│   │   ├── inject.py
│   │   └── budget.py
│   ├── lint/
│   │   ├── extract.py
│   │   ├── match.py
│   │   ├── report.py
│   │   └── enforce.py
│   ├── gate/
│   │   ├── classify.py
│   │   ├── preconditions.py
│   │   └── decision.py
│   ├── llm/
│   │   ├── client.py                 # ctx.llm adapter + budgets
│   │   ├── schemas.py
│   │   └── prompts.py
│   ├── hermes/
│   │   ├── hooks.py
│   │   ├── middleware.py
│   │   ├── tools.py
│   │   ├── schemas.py
│   │   ├── slash_commands.py
│   │   └── cli.py
│   └── data/
│       ├── defaults.yaml
│       ├── action-policies.yaml
│       ├── source-profiles.yaml
│       └── migrations/
│           └── 0001_initial.sql
├── docs/
│   ├── architecture.md
│   ├── event-format.md
│   ├── configuration.md
│   ├── threat-model.md
│   ├── operations.md
│   ├── requirements-traceability.md
│   └── adr/
│       ├── 0001-hermes-integration-surface.md
│       ├── 0002-event-sourced-sqlite.md
│       └── 0003-plugin-only-enforcement-limits.md
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── e2e/
│   ├── properties/
│   ├── fixtures/
│   └── conftest.py
├── evaluations/
│   ├── suite_a_grounding/
│   ├── suite_b_badha/
│   ├── suite_c_agent_failures/
│   ├── suite_d_linter/
│   ├── ablations.py
│   └── report.py
└── scripts/
    ├── check_hermes_contract.py
    ├── replay_ledger.py
    ├── verify_stage.py
    └── smoke_install.py
```

Do not store mutable runtime data in the installation directory. Use
`hermes_constants.get_hermes_home()` and place profile-local data under:

```text
$HERMES_HOME/belief-ledger-pramana/
├── config.yaml
├── ledger.sqlite3
├── evidence/          # only when payload persistence is enabled
├── exports/
└── locks/
```

Create directories with mode `0700` and data files with mode `0600` where the
platform supports POSIX permissions. `HERMES_HOME` is already profile-specific;
do not append the profile name again.

## 8. Configuration contract

On first successful load, atomically copy packaged defaults to
`$HERMES_HOME/belief-ledger-pramana/config.yaml` if no file exists. Do not ask a
setup question. Configuration precedence is:

1. Explicit `BELIEF_LEDGER_PRAMANA_CONFIG` path, if set.
2. Profile-local file under `HERMES_HOME`.
3. Read-only packaged defaults.

Define and validate a versioned schema with at least these sections:

```yaml
schema_version: 1
enabled: true
mode: enforce                  # observe | warn | enforce
default_stakes: med

storage:
  database: null               # null -> profile-local default
  evidence_mode: excerpt       # hash_only | excerpt | full
  max_excerpt_chars: 16000
  redact_secrets: true
  busy_timeout_ms: 5000

context:
  max_chars: 8000
  max_beliefs: 50
  max_graph_depth: 4
  pending_only_when_relevant: true
  retraction_ttl_turns: 3
  relevance: fts5             # lexical fallback if FTS5 unavailable

ingestion:
  lazy_claim_extraction: true
  max_claims_per_evidence: 24
  max_unpromoted_per_request: 2
  max_atomic_claim_words: 40
  near_duplicate_threshold: 0.92
  trusted_workspace_files: false

verification:
  max_llm_calls_per_turn: 3
  max_llm_calls_per_episode: 30
  max_input_tokens_per_episode: 80000
  max_output_tokens_per_episode: 12000
  structured_timeout_seconds: 15
  critical_human_confirmation: true

lint:
  low: annotate
  med: rewrite_once
  high: block
  critical: block
  max_rewrite_attempts: 1
  pending_marker: "(unverified)"

gating:
  enabled: true
  unknown_tool_policy: conservative
  fail_closed_at: high
  allow_human_approval: true

priority:
  integrity_rank: {trusted: 2, semi: 1, untrusted: 0}
  type_rank: {}                # fully populated packaged defaults from spec §4.2
  domain_profiles: {}

trust:
  matrix: {}                   # fully populated packaged defaults from spec §5.2
  yogyata:
    min_coverage: 0.85
    min_recall: 0.85

perishability_ttl:
  stable_seconds: null
  slow_seconds: 2592000
  fast_seconds: 86400
  live_seconds: 0
```

Populate the omitted maps with the exact defaults from the reference
specification. Unknown keys are warnings in `observe` mode and errors in
`enforce` mode. Invalid safety-critical configuration must put the plugin in a
visible degraded state; do not silently fall back to a permissive policy.

Configuration reload may be mtime-based at safe hook boundaries. A single
turn uses one immutable config snapshot so a mid-turn edit cannot give the
compiler and action gate different policies.

## 9. Domain and persistence design

### 9.1 Domain model

Implement every enum and entity in section 2 of the reference specification.
Add these operational records without changing the specified semantics:

- `Episode`: Hermes session/task identity, platform, model, default stakes,
  current turn, lifecycle timestamps, compatibility mode, and budgets.
- `Event`: sequence, event ID, episode ID, timestamp, kind, schema version,
  aggregate type/ID, correlation IDs, causal event, payload, previous hash,
  and event hash.
- `IngestionSupport`: the validity record that links basic evidence to a basic
  belief and can itself be undercut.
- `Conflict`: unresolved REBUT pair, normalized scope, verification task, and
  open/resolved state.
- `RetractionNotice`: defeated root, cause, affected descendants, created
  turn, TTL, and acknowledged/expired state.
- `RenderedBelief`: records that a belief ID was actually shown to the model;
  only rendered IN→OUT transitions require notices.
- `ComponentVerdict`: extractor/linter/NLI/chain-auditor result represented as
  ANUMĀNA from that component, satisfying R5.
- `LlmUsage`: purpose, provider/model, tokens, cost when reported, latency, and
  outcome.

Use timezone-aware UTC datetimes and canonical ISO-8601 serialization. IDs must
be opaque, URL/CLI safe, collision resistant, and visibly typed (`ev_`, `e_`,
`b_`, `j_`, `d_`, `vt_`, `src_`, `rn_`). Do not derive authorization or trust
from an ID prefix.

### 9.2 Event store and projections

SQLite is the source of truth. Use WAL mode, `foreign_keys=ON`, a configured
busy timeout, and explicit transactions. The `events` table is append-only;
install triggers that reject UPDATE and DELETE. Store canonical JSON and a
SHA-256 hash chain over `(previous_hash, canonical_event_without_hash)` so
accidental history mutation is detectable. This is tamper-evidence, not a
signature or remote-attestation claim.

Use the reference specification's tables as the baseline and add:

- `episodes`
- `event_heads`
- `ingestion_supports`
- `conflicts`
- `retraction_notices`
- `rendered_beliefs`
- `source_roots`
- `content_fingerprints`
- `component_verdicts`
- `llm_usage`
- `schema_migrations`
- an FTS5 virtual table over active belief content when available

Every command that changes state must append events and update projections in
one transaction. No service may update a projection directly. `replay` must be
able to delete/rebuild only projections from the event log and yield the same
canonical state hash. Keep migrations forward-only; create a pre-migration
backup and validate the event hash chain before applying one.

### 9.3 Evidence handling and privacy

`transform_tool_result` sees Hermes' post-truncation/post-redaction string in
the audited version. Hash that complete observed string. The default
`excerpt` mode stores a bounded, additionally redacted excerpt plus offsets
and the full hash. `hash_only` keeps metadata without recoverable payload.
`full` is explicit operator opt-in.

In `hash_only` mode, do not promote content claims that require a recoverable
citation span; only wrapper beliefs and metadata-level observations may be
admitted. In `excerpt` mode, a claim whose validated span falls outside the
stored excerpt remains unpromoted.

Never store provider credentials, authorization headers, raw environment
dumps, or Hermes auth files. Redaction must run before persistence and must
emit an event saying that data was redacted without retaining the secret.
Evidence spans are accepted only when they index the persisted canonical
payload/excerpt; model-returned spans are checked deterministically.

### 9.4 Episode identity and concurrency

Resolve an episode key in this order:

1. Non-empty `session_id`.
2. A `turn_id -> session_id` mapping established at `pre_llm_call`.
3. Non-empty `task_id` for RL/headless contexts.
4. A process-local synthetic one-shot ID that is never reused across calls.

Persist the resolution and correlation fields. Never merge two anonymous
episodes merely because they use the same model/platform.

Hermes can run parallel tool calls and multiple threads; gateway/kanban work
can also span processes. Use:

- a small process-local lock per episode around graph mutation/relabeling;
- a new SQLite connection per thread/operation or a tested thread-local pool;
- `BEGIN IMMEDIATE` for event batches;
- WAL and retry-with-jitter only for `SQLITE_BUSY`, within the configured
  timeout;
- idempotency keys based on `(episode, tool_call_id, hook, payload_hash)` so a
  retried callback cannot duplicate evidence;
- no module-global mutable session singleton.

Never hold a SQLite transaction, episode lock, or projection lock across a
host LLM call, Hermes tool dispatch, or human-approval wait. Persist the work
item, release locks, perform the external/slow operation, then reacquire and
apply the result only if the task and input state are still current.

## 10. Core ledger behavior

### 10.1 Admission and content normalization

Implement the validity registry in specification section 3. A belief is not
admitted until its type-specific conditions pass. Enforce atomicity,
self-contained wording, explicit qualifiers, target length, and dedup rules.
Normalization must preserve the original proposition in the event payload and
store the canonical form separately.

Basic beliefs have an `IngestionSupport`; derived beliefs have one or more
`Justification` records. Reject a justification that would create a cycle in
the premise graph, returning the exact path that closes the cycle. Defeat
edges may cycle.

### 10.2 Wrapper/content separation

Every tool result produces:

1. One immutable evidence record.
2. A PRATYAKṢA wrapper belief limited to what Hermes directly observed about
   the tool execution.
3. Zero or more content beliefs, normally ŚABDA, whose source is the actual
   domain/document/user/model rather than the wrapper tool.

Implement explicit adapters for at least:

- terminal/shell and code execution;
- file read/search/list tools;
- web search/extract/browser tools;
- document/retrieval/memory tools;
- delegated-agent results;
- plugin tools themselves.

Unknown tools receive only a conservative wrapper belief until a registered
adapter or validated structured extraction establishes content provenance.
Text that looks like an instruction inside untrusted content is never treated
as a command; anti-injection remains Hermes/the harness's responsibility.

### 10.3 Provenance roots and independence

Define a canonical root for corroboration:

- web: normalized registrable domain plus publisher identity when known;
- document: content hash plus declared origin;
- user: the current user identity/channel, not each message;
- tool observation: concrete tool/environment identity;
- model/subagent: model/component identity; a child summary remains ŚABDA and
  does not inherit claimed sources without a signed ledger extension;
- prior ledger: original source root and type, with re-entry transport marked.

Two beliefs increase corroboration only when their roots differ and their
normalized contents are not near-duplicates. Mirrors, syndicated copies, and
chunks of the same document count once.

### 10.4 Absence and yogyatā

Do not create ANUPALABDHI solely because a search returned no rows. Require a
successful, non-truncated search plus recorded corpus, scope, query parameters,
coverage, and recall estimates meeting configured thresholds. A failed or
under-qualified negative search emits `SEARCH_FAILED` about the search, not a
world-level absence belief. Any later positive finding defeats an admitted
absence regardless of the normal priority tuple.

### 10.5 Qualifier reconciliation and contradiction detection

Canonicalize at least `as_of`, validity interval, scope, jurisdiction,
perspective, units, version, and assumptions before considering REBUT. Two
claims with disjoint temporal or scoped qualifiers may coexist.

Avoid all-pairs comparison:

1. Block candidates with FTS/entity keys and shared predicate/topic signals.
2. Reconcile qualifiers deterministically.
3. Apply deterministic opposites/equality/unit rules where possible.
4. Use a structured `ctx.llm` contradiction classifier only for remaining
   candidates and only within budget.
5. Validate the classifier result and record it as a component ANUMĀNA.

### 10.6 Priority, defeat, and fixed point

Implement the lexicographic priority tuple and fixed rules from specification
section 4.2. Make every rank/config input visible in an explanation trace.
Never use scalar confidence to decide defeat.

The relabeler must:

- run after every graph mutation over the affected subgraph;
- determine live justifications from IN premises and active undercuts;
- propagate loss of support;
- activate winning REBUTs only from currently IN attackers;
- put equal/incomparable conflicts into PENDING with a verification task;
- detect repeated global/local states or the configured iteration ceiling and
  convert the involved defeat cycle to saṃśaya rather than oscillate;
- reinstate a belief in the same fixed-point run when its winning attacker
  loses IN status;
- append a cause-bearing event for every status transition;
- be deterministic for the same event log and configuration snapshot.

Use property tests over generated finite DAGs/defeat graphs for termination,
replay determinism, no IN derived belief with all justifications dead, and
reinstatement.

### 10.7 Retraction protocol

When a belief previously recorded in `rendered_beliefs` changes IN→OUT:

1. Compute all affected descendants.
2. Append a retraction notice with the winning cause and descendants.
3. Prioritize it in compiled context for the configured TTL.
4. Run the linter against later accepted responses to determine whether they
   still depend on the defeated subgraph.
5. Acknowledge/expire the notice only by an explicit event.

The context must retract the subgraph, not merely state that one sentence was
wrong.

## 11. Trust, verification, and āpta

### 11.1 Trust admission

Encode the exact source × stakes matrix from specification section 5.2 in
packaged YAML and test every cell. Effective stakes are the maximum of episode
stakes, belief stakes, and the action for which the belief is a precondition.
The model-visible `pramana_set_stakes` behavior, if later added, may only
increase stakes. Only an explicit user slash/CLI command may lower them.

### 11.2 Verification scheduler

Persist verification tasks and budgets. Prefer passive and deterministic work:

- `cross_source`: complete automatically as independently rooted matching
  beliefs arrive.
- `tool_recheck`: expose the needed observable/tool suggestion in context; the
  agent calls the ordinary Hermes tool under the normal gate. Do not launch
  arbitrary tools from a background thread in v0.x.
- `chain_audit`: run the trairūpya/hetvābhāsa structured audit through
  `ctx.llm`, with deterministic premise-status checks first.
- `human`: use an explicit chat confirmation belief or, for an imminent tool
  action on the audited Hermes version, the `pre_tool_call` `approve` directive.

Do not recursively dispatch a Hermes tool from inside its own tool-result hook.
If future work uses `ctx.dispatch_tool`, add a `ContextVar` reentrancy guard,
separate verification budget, and contract tests first.

When budget is exhausted, leave the belief PENDING and render the specified
unverified marker. Never silently promote it.

### 11.3 Source learning

Use smoothed Beta-style counters per `(source_root, domain)` for confirmed and
defeated claims. Map the posterior to a bounded effective competence used only
at the priority tuple's reliability position. Every update is an event and is
reversible only through a compensating event. Apply floors/ceilings and a
minimum sample count so one outcome cannot swing a source from minimum to
maximum reliability.

## 12. Context compiler and injection

### 12.1 Selection

For each provider request:

1. Use the current user query, pending tool intent, and recent tool result as
   the relevance query.
2. Retrieve via SQLite FTS5; use a deterministic lexical scorer when FTS5 is
   unavailable.
3. Expand selected derived beliefs to their premises and selected conflicts to
   both sides.
4. Order mandatory content as:
   live retractions, open conflicts, current action preconditions, then other
   beliefs by priority and relevance.
5. Include PENDING only when directly relevant and always mark it.
6. Exclude OUT and QUARANTINED from usable beliefs; they may appear only in an
   explicitly labeled audit/retraction section.
7. Enforce graph depth, count, and character budgets deterministically.

Record which beliefs were rendered in the same transaction as a
`CONTEXT_COMPILED` event. Compression may summarize typed subgraphs but must
preserve root IDs/types; never flatten to untyped prose.

### 12.2 Rendering

Implement the line grammar, active-ledger section, open-conflict section,
retraction section, and generation contract from specification section 6.2.
Keep stable snapshot tests for ASCII and Unicode rendering. IDs must be easy to
cite (`[b_...]`), and every line must contain enough source/justification data
to interpret its status without looking at logs.

Add a machine marker around the injected block for diagnostics, but do not
trust or parse identical markers from user content. The middleware is operating
on a fresh request copy and should append exactly one internally generated
block per call.

## 13. Model tools and commands

### 13.1 Model-visible tools

Register these tools under toolset `belief_ledger_pramana`:

1. `pramana_record_inference`
   - Inputs: atomic content, kind (`anumana|arthapatti|upamana`), premise IDs,
     warrant, qualifiers, perishability, optional stakes.
   - Validates that premises exist and are IN, the warrant is non-empty, and no
     cycle would be created.
   - For ARTHĀPATTI, requires an IN explanandum and recorded alternatives.
   - Never accepts basic-source types.

2. `pramana_query`
   - Inputs: query, optional statuses/types, limit, graph expansion flag.
   - Returns concise JSON records and never returns full stored evidence by
     default.

3. `pramana_explain`
   - Inputs: belief ID and optional depth.
   - Returns provenance, validity, live/dead justifications, priority trace,
     defeat edges, transition causes, and verification state.

4. `pramana_request_verification`
   - Inputs: belief ID and allowed method.
   - Creates/deduplicates a bounded verification task; it does not pretend that
     scheduling equals confirmation.

The compiler contract should teach the model when these tools are useful;
their schema descriptions must not tell the model to call them reflexively on
every sentence.

### 13.2 In-session slash command

Register `/ledger` with subcommands parsed from its raw argument string:

- `/ledger status`
- `/ledger conflicts`
- `/ledger retractions`
- `/ledger belief <id>`
- `/ledger stakes <low|med|high|critical>`
- `/ledger export [jsonl|markdown]`
- `/ledger help`

Slash handlers return strings, are safe in CLI and gateway, and do not rely on
`ctx._cli_ref`.

### 13.3 Operator CLI command

Register `hermes belief-ledger` with:

- `doctor`
- `config show|path|validate|init`
- `db status|migrate|verify-chain|replay`
- `episode list|show|export`
- `purge --episode <id> --confirm <id>`
- `evaluate --suite <a|b|c|d|all> --offline`

`doctor` must be offline and non-mutating except for a temporary write test in
the plugin state directory. It checks the Hermes version/capabilities,
enablement, middleware availability, hook competition/order, Python version,
config validity, database migration/hash state, FTS5 availability, file
permissions, and tool registration. It must not make a paid LLM call.

## 14. Lazy model-assisted components

### 14.1 Claim extractor

At tool ingestion, store evidence and the wrapper belief synchronously. Put
content evidence in an unpromoted inbox. Before context compilation, select at
most `max_unpromoted_per_request` items relevant to the active query and call a
structured extractor.

The extractor schema must return atomic claims with:

- content;
- proposed pramāṇa/content-source class;
- source URL/document identity when present;
- exact span start/end and exact excerpt;
- qualifiers;
- domain;
- perishability;
- whether the text is asserting, quoting, speculating, or instructing.

Deterministically reject spans that do not match, conjunctions that exceed the
atomicity policy, unsupported source identities, instructions presented as
claims, and invalid enum values. Keep the evidence unpromoted on failure and
record the extractor's verdict/failure as required by R5.

### 14.2 Contradiction classifier

The structured result is one of `rebut`, `compatible`, `scope_mismatch`, or
`uncertain`, with normalized scopes and a short basis. Only `rebut` after local
validation creates an edge. `uncertain` may create a verification task but not
a defeat edge.

### 14.3 Chain auditor

Implement the appendix A checklist as structured booleans plus evidence IDs.
Local premise-status validation handles asiddha before the LLM call. The model
may propose counterexamples, but any claimed external fact must itself become
evidence/belief before it undercuts a chain.

### 14.4 Linter model calls

Use deterministic citation parsing first. Call a model only for claim
extraction/entailment pairs that cannot be resolved locally. Limit candidate
beliefs before the call. Never ask the linter model to decide ledger status or
priority; it only assesses text-to-belief support. Record linter precision
metrics against suite D.

## 15. Output linting and enforcement

For an accepted candidate response:

1. Extract declarative factual claims while ignoring code literals, explicit
   questions, and clearly marked speculation.
2. Parse cited belief IDs and reject missing, OUT, or QUARANTINED citations.
3. Require the configured marker for PENDING citations.
4. Match claims to IN beliefs with deterministic exact/normalized matching,
   followed by bounded semantic entailment when necessary.
5. Classify each claim as grounded, inferible, pending-marked, or vikalpa.
6. If inferible, create ANUMĀNA only when explicit IN premises and a warrant
   can be recovered and validated; otherwise leave it vikalpa.
7. Store the complete report and component verdict as events/beliefs.

Enforcement policy:

- LOW: deliver the answer, append a compact grounding warning only if
  configured, and record violations.
- MED: perform at most one bounded rewrite using the current ledger; lint the
  rewrite deterministically. If it still fails, replace unsupported clauses
  with explicit speculation/unverified markers or a compact omission notice.
- HIGH/CRITICAL: replace the candidate response with a safe block report that
  lists missing claim support and suggested read-only verification steps. Do
  not deliver the unsupported factual claims.
- Coding turns: before final transform, `pre_verify` may return one continuation
  instruction on attempt 0 so the agent can acquire grounding. On subsequent
  attempts it must return `None` and let final policy decide.

Add a reentrancy guard around linter-triggered `ctx.llm` calls and enforce one
rewrite attempt. Failure of the linter itself is a HIGH/CRITICAL block and a
LOW/MED degraded warning, never an infinite retry.

## 16. Action gate

### 16.1 Action-policy registry

Hermes does not provide universal stakes metadata, so ship a versioned registry
for audited built-ins and allow operator extensions. Rules match exact tool
names first, then anchored regexes, and specify:

- base stakes;
- whether the action is read-only or effectful;
- precondition templates;
- minimum belief priority;
- whether human approval may satisfy a missing confirmation;
- argument fields that identify target/path/environment.

At minimum classify filesystem writes/patches, terminal execution, messaging,
browser mutations, cron/kanban mutation, memory mutation, delegation, and
plugin-management operations. Terminal classification must inspect the command
conservatively; do not duplicate or weaken Hermes' dangerous-command approval.
The belief gate is additive to Hermes security approvals.

For unknown tools in `conservative` mode:

- names/descriptions/arguments suggesting write, delete, send, publish,
  execute, deploy, approve, purchase, or mutation are HIGH;
- clearly read-only retrieval is MED;
- ambiguity is HIGH in enforce mode and produces an actionable block asking
  for an operator rule, not a guessed ALLOW.

### 16.2 Preconditions and decisions

Implement deterministic resolvers for common preconditions such as target
existence, parent existence, current environment, resource identity, explicit
user confirmation, absence of open conflict, and version/as-of freshness.
Resolvers query the ledger first. A safe local read-only probe may create a
new PRATYAKṢA belief only when the action targets the same local environment;
never use local filesystem state to justify a remote/container action.

Return one of:

- `ALLOW`: all required beliefs are IN and meet minimum priority.
- `APPROVE`: audited Hermes supports approval escalation and explicit human
  confirmation is the only missing critical precondition.
- `BLOCK`: support is missing/pending/conflicted, classification is unknown in
  strict mode, or policy evaluation failed closed.

The block message must include a stable reason code, missing proposition, and
a safe suggested observation, so the model can recover without guessing. Log
arguments only after redaction.

## 17. Failure modes and security posture

Define component health as `healthy`, `degraded`, or `unavailable` and surface
it in every compiled contract and `doctor` output. Required behavior:

| Failure | LOW/MED | HIGH/CRITICAL |
|---|---|---|
| Claim extractor unavailable | Keep evidence unpromoted; warn only when relevant | Do not rely on unpromoted content |
| Context injection shape unknown | Fallback to documented `pre_llm_call` if possible and label degraded | Block effectful actions/final unsupported claims |
| SQLite temporarily busy | Retry within budget; then visible degraded state | Fail closed at gate |
| SQLite corrupt/hash mismatch | Read-only quarantine and recovery instructions | Fail closed |
| LLM budget exhausted | Keep PENDING and mark unverified | Block unsupported use |
| Linter failure | Deliver with degraded warning under configured MED policy | Replace with safe block report |
| Gate exception | Allow only known read-only LOW/MED calls | Explicit block |
| Unsupported Hermes version | Diagnostics-only compatibility mode | Never claim enforcement |

Additional security requirements:

- Never evaluate code from evidence or model output.
- Use parameterized SQL exclusively.
- Bound input, output, graph depth, recursion, event size, and LLM calls.
- Validate paths with resolved containment before reading/writing plugin data.
- Use atomic file replacement for config/export manifests.
- Avoid daemon threads that can outlive Hermes teardown. If a bounded worker is
  added, give it explicit stop/drain hooks and tests.
- Never alter Hermes approvals, credentials, or provider routing.
- Include a threat model explaining that untrusted content typing is not an
  anti-injection boundary.

## 18. Implementation stages and automated gates

Claude Code must execute the following in order, updating
`IMPLEMENTATION_STATE.md` with the phase, commands, exit codes, and key artifact
paths. A green gate advances automatically. A red gate triggers diagnosis and
repair, not a request for human inspection.

### Stage 0 — bootstrap and contract freeze

Tasks:

1. Read this plan and the reference specification completely.
2. Inspect the workspace and preserve unrelated files.
3. Initialize the Python project, `.gitignore`, `CLAUDE.md`, test tooling, and
   deterministic lock file using `uv` if available.
4. Record the Hermes version/commit and source links in
   `HERMES_COMPATIBILITY.md`.
5. Write ADR 0001 with the hook/middleware mapping and ADR 0003 with the known
   final-transform limitations.
6. Create the requirements traceability table with one row for R1–R8 and every
   numbered specification section/appendix.
7. Add a pinned-Hermes contract-test fixture. It may install the exact release
   and separately accept a local checkout path for exact-commit CI.

Automated gate:

```text
python version is within >=3.11,<3.14
pyproject metadata parses
uv lock --check (or equivalent lock validation) passes
scripts/check_hermes_contract.py reports audited version/capabilities
```

### Stage 1 — installable Hermes skeleton

Tasks:

1. Add the manifest, root shim, package entry point, compatibility guard, and
   no-op service container.
2. Register all planned hook/middleware names, tools, slash command, and CLI
   command with safe placeholder responses.
3. Implement config/state path resolution and offline `doctor`.
4. Add structured logging and correlation-ID helpers.
5. Ensure unsupported Hermes versions enter diagnostics-only mode visibly.

Contract tests must use a real pinned `PluginManager` to prove directory and
entry-point loading, enable/disable behavior, registered names, `**kwargs`
forward compatibility, and JSON-string tool results.

Gate:

```text
ruff format --check and ruff check pass
static type check passes
unit + plugin contract tests pass
directory-install smoke test lists and enables the plugin
doctor exits 0 in the pinned test environment
```

### Stage 2 — domain, event store, and replay

Tasks:

1. Implement enums/entities, canonical serialization, IDs, migrations, event
   hash chain, and projections.
2. Implement episode resolution and idempotency.
3. Add config validation and state permissions.
4. Implement export, chain verification, projection replay, and backup before
   migration.
5. Add deterministic fixtures for every entity/event kind.

Gate:

```text
fresh DB, reopen, migration, backup, replay, and hash verification tests pass
generated event sequences replay to identical canonical projection hashes
concurrent writers do not duplicate a tool-call event
events reject UPDATE/DELETE
```

### Stage 3 — v0.1 ledger engine

This stage corresponds to the reference specification's v0.1 hypothesis test.

Tasks:

1. Implement normalization, per-type validity, trust admission, graph cycle
   rejection, priority, manual REBUT/UNDERCUT, relabeling, reinstatement, and
   retractions.
2. Implement manual/model inference recording with strict premise validation.
3. Implement context selection/rendering with lexical/FTS relevance and
   token/character budgets.
4. Implement initial user/tool wrapper ingestion without automatic content
   extraction.
5. Wire per-request middleware injection for all four API modes.

Gate:

```text
all R1/R4/R7/R8 unit and property tests pass
Hermes request-shape snapshot tests pass for all audited API modes
tool result remains byte-for-byte unchanged when ingestion hook returns None
compiled context never contains OUT/QUARANTINED as active support
v0.1 synthetic grounding smoke suite report is produced automatically
```

Tag/package locally as `0.1.0` only after the gate; do not publish.

### Stage 4 — v0.2 automatic ingestion and bādha

Tasks:

1. Add tool adapters, provenance roots, wrapper/content extraction, lazy inbox,
   structured claim extraction, span validation, and semantic dedup.
2. Add yogyatā and `SEARCH_FAILED` behavior.
3. Add candidate blocking, qualifier reconciliation, structured contradiction
   adjudication, priority traces, and automatic defeat/retraction.
4. Implement suite B with scheduled contradictory evidence and exact expected
   winners/descendants.
5. Add source-integrity defaults for Hermes tools, web, files, users, memory,
   and delegated agents.

All model calls in tests use scripted `ctx.llm` fakes. Live-provider tests are
optional markers and cannot gate this stage.

Gate:

```text
wrapper/content, R2/R3/R6/R7, qualifier, and provenance tests pass
suite B wrong-winner rate is zero on deterministic fixtures
all expected descendants retract and later reinstate
LLM malformed/timeout/schema-failure cases remain auditable and bounded
```

### Stage 5 — v0.3 verification, linter, and āpta

Tasks:

1. Implement persistent verification scheduling, passive cross-source
   completion, chain audit, budget accounting, and human-confirmation records.
2. Implement āpta updates and explanation traces.
3. Implement final claim extraction/matching, vikalpa classification, one-shot
   MED rewrite, HIGH/CRITICAL block replacement, and `pre_verify` continuation.
4. Implement retraction acknowledgement based on later output dependence.
5. Create and freeze suite D's labeled fixture format; synthetic labels must be
   sufficient for CI even if a future human-labeled corpus is larger.

Gate:

```text
trust-matrix test covers every source × stakes cell
budget exhaustion never promotes a pending belief
R5 component verdicts are present for extractor/NLI/linter/auditor
suite D precision/recall meets thresholds recorded in evaluation config
MED rewrite is bounded to one attempt; HIGH/CRITICAL never emits fixture vikalpa
```

### Stage 6 — v1.0 action gate and complete Hermes UX

Tasks:

1. Implement action-policy registry, terminal/unknown classification,
   preconditions, fail-closed boundary, and optional approval escalation.
2. Complete model tools, slash commands, operator CLI, exports, doctor, and
   operations docs.
3. Add subagent provenance and all session-boundary behaviors.
4. Add suite C failure injection and the unsafe-action/false-block metrics.
5. Add contract tests for CLI, gateway-shaped callbacks, headless sessions,
   parallel tool calls, transform competition, and profile isolation.

Gate:

```text
every known effectful tool fixture is gated before execution
unknown ambiguous tools block in enforce mode with a recovery suggestion
gate internal failures block HIGH/CRITICAL and do not crash Hermes
CLI/gateway/headless/subagent contract fixtures pass
suites A-D and all ablations produce one versioned report
```

### Stage 7 — evaluation, performance, and hardening

Tasks:

1. Run the full offline evaluation matrix and ablations: flat baseline, types
   only, defeat only, no generation contract, and no gate.
2. Measure tokens, LLM calls, SQLite time, compiler time, hook latency, event
   growth, false blocks, and retraction latency.
3. Add fuzz/property tests for corrupted results, Unicode, large payloads,
   malformed JSON, cyclic attacks, qualifier edges, and concurrent callbacks.
4. Run security/static checks, package-content checks, and upgrade/downgrade
   compatibility tests.
5. Apply the specification's collapse criterion. If typed ledger performance
   does not materially improve suite A within the accepted overhead, preserve
   the measured components that survive ablation and explicitly report the
   collapse decision; do not massage thresholds after seeing results.

Gate:

```text
all mandatory evaluation thresholds pass or an explicit machine-generated
collapse report selects the reduced design allowed by the specification
MED token overhead target is <=35% on the frozen suite
no unbounded hook/LLM loop exists
package and state-path security checks pass
```

### Stage 8 — release candidate, without automatic publication

Tasks:

1. Build wheel and sdist; inspect their file lists automatically.
2. Install each artifact in a clean environment with pinned Hermes.
3. Run directory/Git-layout and entry-point smoke tests.
4. Validate README installation, enablement, configuration, upgrade, export,
   replay, and uninstall instructions by executing documented commands in temp
   homes.
5. Generate changelog, compatibility matrix, SBOM/dependency report, test
   report, evaluation report, and release notes.
6. Create a local `1.0.0rc1` artifact only when all prior gates pass.

Public GitHub/PyPI publication, signing with a user's key, and choosing a legal
license are external actions requiring one brief explicit authorization. If no
answer is available, finish with reproducible local artifacts and checksums;
implementation is still complete.

## 19. Test architecture

### 19.1 Required layers

- **Unit tests:** pure domain functions, configuration, serialization,
  renderers, schemas, source adapters, and decisions.
- **Property tests:** finite graph termination, replay determinism,
  reinstatement, dedup/idempotency, qualifier symmetry, and bounded output.
- **Hermes contract tests:** real pinned plugin manager/middleware helpers and
  fake tool/LLM transports; assert signatures, ordering, return semantics, and
  enablement.
- **Integration tests:** complete user → LLM request → tool → relabel → next
  request → lint sequences with a scripted host LLM.
- **End-to-end tests:** clean temporary `HERMES_HOME`, plugin discovery,
  activation, command registration, and a mocked-provider agent turn.
- **Evaluation tests:** suites A-D and ablations, with versioned JSONL fixtures
  and machine-readable reports.
- **Live tests:** optional, explicitly marked, budget-capped, never part of the
  default gate.

### 19.2 Essential scenarios

At minimum cover:

1. The complete stale-blog/new-tool-observation trace in specification section
   9, including descendant retraction and later linter acknowledgement.
2. Equal-priority contradiction becomes PENDING conflict, not an arbitrary
   winner.
3. A winning attacker becomes OUT and the target reinstates in one relabel.
4. Time-scoped claims coexist after qualifier reconciliation.
5. Web fetch creates a tool wrapper PRATYAKṢA and domain-content ŚABDA.
6. Negative search without detectability creates `SEARCH_FAILED`, not absence.
7. Independent domains corroborate; mirrors/chunks do not.
8. Prior LIVE memory re-entry is not IN until re-observed.
9. Extractor/linter verdicts are themselves auditable ANUMĀNA.
10. Parallel tools produce one evidence batch each and deterministic final
    state regardless of completion order where semantics are independent.
11. Hook retry with the same `tool_call_id` is idempotent.
12. Context is ephemeral: it is present in provider kwargs and absent from the
    persisted original user/tool messages.
13. All provider request shapes remain valid after injection.
14. A competing final transformer makes doctor fail strict-conformance status.
15. Invalid config/database/LLM failures follow the failure table.
16. HIGH effectful action with a missing precondition never reaches its fake
    handler.
17. Human approval denial/timeout remains a block.
18. Every public tool catches exceptions and returns valid JSON.

### 19.3 Standard local gate

Configure one command, such as `uv run python scripts/verify_stage.py all`, to
run the equivalent of:

```bash
ruff format --check .
ruff check .
mypy belief_ledger_pramana
pytest -m "not live_llm" --cov=belief_ledger_pramana --cov-report=term-missing
python scripts/check_hermes_contract.py
python -m build
twine check dist/*
```

Set a meaningful coverage floor for domain, storage, engine, compiler, lint,
and gate modules (target 90% line coverage overall and 100% branch coverage on
priority fixed rules and HIGH/CRITICAL fail-closed decisions). Coverage is a
diagnostic; scenario and property assertions remain the real gate.

### 19.4 CI matrix

Use Python 3.11, 3.12, and 3.13 on Linux. Add Windows path/SQLite tests and a
macOS smoke job when CI resources allow. CI must include:

- pinned Hermes `0.18.2` release tests;
- exact audited-commit contract tests;
- lowest and locked dependency resolution;
- wheel/sdist clean-install tests;
- offline evaluations and artifact upload;
- a non-blocking canary against Hermes `main` that reports contract drift but
  never silently changes the supported range.

### 19.5 Frozen initial evaluation thresholds

Write these values into a versioned evaluation config before running the full
suite. They are initial engineering gates, not claims of scientific consensus:

| Suite/metric | Initial gate |
|---|---|
| A — relative reduction in final-answer vikalpa vs flat baseline | `>=15%` |
| A — MED token overhead | `<=35%` |
| B — wrong winner on deterministic cases | `0` |
| B — expected descendant propagation | `100%` |
| B — retraction latency after decisive evidence | before the next accepted model response |
| C — unsafe effectful actions that reach the fake handler | `0` |
| C — false blocks on labeled safe actions | `<=10%` |
| D — vikalpa detector precision | `>=0.90` |
| D — vikalpa detector recall | `>=0.85` |
| Event replay/hash verification | `100%` exact |

If a threshold changes, commit the rationale before looking at the new run's
result and retain both configurations/reports. Apply the specification's
collapse rule when suite A or overhead fails; do not ask for a subjective human
pass between runs.

## 20. Claude Code CLI execution protocol

Create `CLAUDE.md` with these rules so the coding agent can continue
autonomously:

1. Read this plan and the reference specification fully before implementation.
2. Work stage by stage; do not ask for human review after a green stage.
3. Maintain `IMPLEMENTATION_STATE.md` with evidence, not subjective claims.
4. Run the narrowest relevant tests after each change and the complete stage
   gate before advancing.
5. On a failure, inspect logs, add a regression test when appropriate, fix, and
   rerun. Do not weaken an assertion merely to make it green.
6. Use only offline/scripted LLM fixtures unless a live budget was explicitly
   authorized.
7. Preserve the reference specification and this plan; changes to either
   require an ADR explaining why.
8. Do not modify a user's Hermes installation or real `HERMES_HOME` during
   development. All tests use temporary homes.
9. Do not publish, push, create remote releases, or purge real data without
   explicit authorization.
10. Do not use unsafe blanket permission bypasses. Configure/approve the small
    set of routine dependency, formatter, test, build, and temporary-directory
    commands once at the start if the CLI requires command permissions.

Suggested initial Claude Code instruction:

```text
Read belief-ledger-pramana-hermes-plugin-plan.md and
belief-ledger-pramana-spec-v0.1.md completely. Implement the plan in order.
Continue automatically through every stage whose automated gate passes. Keep
IMPLEMENTATION_STATE.md current. Use the pinned Hermes contract and offline LLM
fixtures. Ask me only for an external authorization or a genuinely destructive
migration decision; do not pause for human verification between stages.
```

Reasonable defaults that must not trigger a question:

- package/plugin name: `belief-ledger-pramana`;
- Python import package: `belief_ledger_pramana`;
- default mode: `enforce`;
- default episode stakes: MED;
- storage: profile-local SQLite with excerpt evidence;
- model routing: active Hermes provider/model;
- publishing: no;
- live paid evaluation: skipped;
- license metadata: deferred until public-release authorization if no license
  has already been supplied.

## 21. Requirements traceability and definition of done

`docs/requirements-traceability.md` must map each requirement to code and at
least one test/evaluation. Include:

- R1–R8;
- every entity and content rule in section 2;
- every pramāṇa validity/defeater rule in section 3;
- REBUT, UNDERCUT, priority, saṃśaya, fixed point, reinstatement, and
  retractions in section 4;
- every trust-matrix cell and verifier in section 5;
- selection/rendering/compression in section 6;
- all ingestion/lint/gate behavior in section 7;
- all five harness integration points in section 8;
- the end-to-end trace in section 9;
- suites A-D, ablations, overhead, and collapse criteria in section 10;
- appendix A audit categories and appendix B concept mapping;
- every Hermes technical requirement in section 5 of this plan.

The project is done when all of the following are true:

- A clean install is discovered, explicitly enabled, loaded, and shown by
  `/plugins`/doctor.
- The package works through directory/Git and pip entry-point paths.
- Full mode runs only on a verified compatible Hermes contract; degraded mode
  is obvious.
- The event log replays deterministically and detects mutation.
- The complete five-point middleware behavior works in automated Hermes
  integration tests.
- Every type validity rule, defeat rule, trust rule, and fixed priority rule is
  covered.
- Retractions propagate and are rendered until acknowledged/expired.
- Context is relevant, bounded, typed, and ephemeral.
- Tool outputs remain unmodified unless a future explicit transform feature is
  enabled.
- Final-output and action policies fail closed at HIGH/CRITICAL.
- The plugin makes no direct provider credential assumptions and respects
  `ctx.llm` trust/budget gates.
- Suites A-D and ablations produce reproducible machine-readable reports.
- The documented collapse criterion has been evaluated honestly.
- Offline `doctor`, full tests, static checks, build, artifact installation,
  and documentation command tests pass.
- Local release-candidate artifacts and checksums exist; no external release
  was performed without authorization.

## 22. Risks to track during implementation

Maintain these as measurable risks, not generic TODOs:

| Risk | Detection | Required mitigation |
|---|---|---|
| Claim granularity explosion | beliefs/evidence and claims/turn metrics | atomicity validation, per-evidence/episode caps, lazy promotion |
| Extractor false claims/spans | suite fixtures and span validation failures | deterministic span checks, quarantine invalid output, R5 stats |
| False contradiction | suite B wrong-winner rate | qualifier-first pipeline and uncertain→verification |
| Linter false positives/negatives | suite D precision/recall | deterministic citations first, bounded semantic checks, measured thresholds |
| Hook latency/cost | per-purpose usage/latency metrics | lazy work, strict budgets, passive verification |
| SQLite contention | busy/retry metrics and concurrency tests | WAL, short transactions, idempotency, per-episode locks |
| Hermes contract drift | pinned + main-canary contract CI | narrow version range and explicit compatibility mode |
| Competing transforms | doctor and contract fixture | warn/fail strict conformance; document precedence requirement |
| Action misclassification | suite C false-block/unsafe-action rates | versioned policies, conservative unknown behavior, operator extension |
| Sensitive evidence retention | secret fixtures and package/state scan | default excerpts, redaction, permissions, no auth/env persistence |
| Context budget pressure | render size/token overhead metrics | mandatory ordering, graph-aware compression, 8k hard cap |
| Discrete priority inadequacy | suite B errors and ablations | config-driven ranks; consider Bayesian extension only after evidence |

This plan intentionally delivers the smallest faithful Hermes integration
first, then adds model-assisted automation behind deterministic validation and
budgets. The typed context compiler, structural retraction, and pre-action gate
remain the product; dashboards, embeddings, remote services, and multi-agent
signed-ledger exchange are later extensions, not prerequisites.
