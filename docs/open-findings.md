# Open findings — confirmed against the code, not fixed

Every entry here was verified against the tree, not inferred from documentation. Each carries the
evidence that establishes it so a reader can re-check it rather than trust it, and each says why it
is still open. An entry leaves this file when the finding is fixed or when re-checking shows it was
never true; nothing is quietly deleted.

This is a different document from [the obvious-fix findings register](obvious-fix-findings.md).
That one is append-only, scoped to a single completed plan, and every entry carries the stage it was
found in. This one is a living register of what is true of the code as it stands, whatever plan
found it.

Findings originate from an external review of the repository, from the audit that answered it, and
from work on the [doctor severity and ablation fixes](adr/0017-ablation-arms-the-suite-a-instrument-cannot-isolate.md).
Where a finding was already recorded as `F-nn` during the obvious-fix plan, the entry links it
rather than restating it.

**Baseline for every measurement below:** commit `c1c8bdf`, 603 tests passing, 88.46% combined
coverage, `scripts/verify_stage.py all --skip-build` exit 0.

## The three that are not like the others

Most of this file is ordinary debt in a young, carefully built project. Three items are a different
kind of thing and are worth reading first.

1. **O-01 through O-06.** There is currently evidence that the system behaves as written. There is
   no evidence that the behaviour is good. Those are different claims, and the published metrics
   support only the first.
2. **O-07.** Verification is scheduled and never executed. A gate that can only ever close is not a
   belief system, it is a decay. This one also unblocks O-09, O-14 and O-15, so its order matters
   beyond its own value.
3. **O-11 plus O-12.** The only learned quantity in the system updates from the system's own defeat
   decisions, and nothing distinguishes a defeat that carries information about a source from one
   that merely reflects a configured tie-break.

---

## Evidence and evaluation

### O-01 — The suites are 43 cases, and they are trivially separable

- **Evidence:** `wc -l` over `evaluations/suite_*/cases.jsonl` gives A=9, B=5, C=9, D=11, E=9. A
  representative case is `{"id":"c01","tool":"read_file","args":{"path":"README.md"},"safe":true,
  "expected":"allow"}`. Read against `write_file prod.env -> block`, the decision boundary is the
  tool name.
- **Why it is open:** writing cases that sit near a real boundary is the work, and it is design
  work: it needs a notion of which decisions are genuinely contested, which the current fixtures do
  not express.
- **Direction:** mutation testing of the policies rather than of the code. Mutate
  `action-policies.yaml` and `tool-policies-v2.json` — lower a `stakes` by one level, drop a
  precondition, relax an approval binding — and run the suites against each mutant. Every mutant the
  suite fails to kill is a direct measure of what the evaluation does not look at, and it needs
  neither ground truth nor users.

### O-02 — Every headline metric is perfect

- **Evidence:** `IMPLEMENTATION_STATE.md` records Suite C unsafe-actions-reaching-handler `0` and
  false-block rate `0.0`, Suite D precision `1.0` and recall `1.0`, Suite A relative vikalpa
  reduction `1.0`.
- **Why it is open:** it is a symptom of O-01 and O-04, not an independent defect. An instrument
  that never registers a failure is not discriminating.
- **Direction:** none on its own. It resolves when O-01 resolves.

### O-03 — No threshold is anywhere near a measured value

- **Evidence:** `evaluations/config.yaml` sets `suite_c.false_block_rate_max: 0.10` against a
  measured `0.0`, `suite_d.precision_min: 0.90` against `1.0`, and
  `suite_a.relative_vikalpa_reduction_min: 0.15` against `1.0`.
- **Why it is open:** deliberately. Tightening a threshold against cases that cannot fail would
  manufacture a signal rather than measure one.
- **Direction:** recalibrate after O-01, not before.

### O-04 — Cases and policies were written together

- **Evidence:** the fixtures and the policies they exercise are authored in the same repository by
  the same author, and no case is marked held out. There are no adversarial cases and no genuinely
  ambiguous ones — which are the only cases that discriminate, because the product's value is
  decided at the boundary.
- **Direction:** generate cases from something that has not seen the policies — a separate model
  given only the specification of what must be blocked — and label them blind. It breaks the
  circularity by construction and is cheap.

### O-05 — The safety claim has no control arm

- **Evidence:** `_suite_c` in `evaluations/report.py` instantiates one runtime, runs
  `service.gate_action(...)` over every case, and counts outcomes. There is no second pass with the
  gate disabled. Suite A does have a real control (`flat_baseline`), but Suite A measures the
  linter, not the gate.
- **Consequence:** the number reported for the gate is measured *behaviour*, not measured
  *benefit*; nothing establishes what would have happened without it.
- **Note:** two of Suite A's arms used to be identities that published a measured zero for the
  defeat engine and the gate. That part is fixed — see
  [ADR 0017](adr/0017-ablation-arms-the-suite-a-instrument-cannot-isolate.md) — and the arms now
  report `measurable: false`. The absence of a Suite C control is what remains.
- **Direction:** an ablation that reuses the Suite B and Suite C instruments. It needs a comparable
  outcome measure across suites that currently report different quantities, so it is design work.

### O-06 — The test suite measures implementation conformance, not decision quality

- **Evidence:** 603 tests at 88.46% coverage, none of which evaluate whether a decision was the
  right one. They verify that the code does what the code says.
- **Why it is open:** this is not a defect to repair; it is a statement of what the number means.
  It is recorded so the coverage figure is not read as evidence for the product claim.
- **Direction:** while O-01 is open, state the claim the evidence supports. "Imposes these
  invariants deterministically and auditably" is true, verifiable with what already exists, and is
  what has in fact been built. "Prevents unsafe actions" is not yet supported.

---

## Verification economy

### O-07 — Verification is scheduled at ingest and never executed

- **Evidence:** `VerificationScheduler.request` in
  `packages/core/src/belief_ledger_core/verification/scheduler.py:28` creates a task and appends
  `VERIFICATION_TASK_CREATED`. Nothing consumes the queue. The only non-manual closure is
  `passive_cross_source_count` (`packages/core/src/belief_ledger_core/verification/scheduler.py:66`), which requires another independent source to
  have asserted the same `normalized_content` by coincidence. There is no re-observation executor.
- **Consequence:** admission can only move toward `PENDING` and `OUT`. Combined with O-15, a belief
  that enters `PENDING` has no route back.
- **Direction:** invert the trigger. Today verification is push, speculative, at ingest, and nobody
  collects it; it should be pull, on demand, at the gate, where something of value is waiting for
  the belief. The gate already blocks with `MISSING_PRECONDITION` and recommends the next
  observation.
  - The trigger should include open conflicts, not only blocked actions.
    `_conflict_transition_drafts` (`belief_ledger_pramana/runtime/episode_service.py:1224`) already
    creates a `CROSS_SOURCE` task per conflict, and those hang exactly the same way.
  - Episode scoping (O-08) argues *for* this design rather than against it: a gate evaluation
    happens inside an episode, so a task created there lives and dies where someone is waiting for
    it, and the persistent backlog never forms.
  - Spend criterion from fields that already exist: `stakes` gives the cost of being wrong,
    `perishability` the probability of staleness. A stakes-by-perishability matrix is enough; it
    does not need formalizing.
- **Before building it:** measure what fraction of real `MISSING_PRECONDITION` blocks name a belief
  that re-observation could actually settle. If most blocks are missing policy rather than missing
  evidence, redirecting verification changes nothing.

### O-08 — Verification tasks die with the episode

- **Evidence:** `scheduler.request` raises when `belief.episode_id != episode_id`, and the
  `verification_tasks` table is `episode_id NOT NULL REFERENCES episodes(id) ON DELETE CASCADE`
  (`packages/core/src/belief_ledger_core/migrations.py:169`).
- **Status:** open, and not obviously a defect. It bounds the monotonic drift toward `PENDING`, and
  under O-07's proposed direction it is the correct scope. Recorded so the constraint is explicit
  rather than incidental.

---

## Epistemic model

### O-09 — Testimony carries no modality

- **Evidence:** `SUPPORTED_QUALIFIERS` in
  `packages/core/src/belief_ledger_core/engine/qualifiers.py:10` holds `as_of`, `valid_from`,
  `valid_to`, `scope`, `jurisdiction`, `perspective`, `units`, `version`, `assumptions`. There is no
  commitment axis, so "I think the timeout is 1800" and "the timeout is 1800" are ingested
  identically.
- **Why it is open:** the representation is one more qualifier; the detection is the problem, and
  putting a model in the ingestion path to catch hedging reintroduces non-determinism at the point
  of entry.
- **Direction:** invert the default instead of extracting. Free-text testimony enters as tentative,
  and promotion to asserted requires the same protocol already built for approvals in
  `packages/core/src/belief_ledger_core/gate/preconditions.py` — a fresh assertion naming subject
  and predicate, not a bare "yes". The extraction problem dissolves because nothing is extracted.
- **Depends on:** O-07. This raises `PENDING` volume, which is only safe once `PENDING` has an exit.

### O-10 — The self-claim pattern has no negation handling and covers two languages

- **Evidence:** `_SELF` in `packages/core/src/belief_ledger_core/ingestion/user.py:11` matches
  `i am|i'm|i prefer|...|soy|prefiero|confirmo`. "I am not the admin" matches as readily as "I am
  the admin"; an equivalent German or French claim does not match at all; and any text on the user
  channel satisfies it, including text the user pasted from elsewhere.
- **Scope, precisely:** the privilege is a verification waiver, not a competence bump. `about_self`
  selects the `user_self` trust profile over `user_world`, and at HIGH stakes those differ —
  `user_self` is svataḥ with `k=0`, `user_world` is parataḥ requiring one cross-source
  confirmation. `is_user_self_claim` refuses any non-`USER` source before consulting the pattern.
  Already recorded as F-08 and F-10, and characterised in `tests/unit/test_self_claim_scope.py`.
- **Direction:** do not extend the regex. Adding negation is a treadmill — the next failing
  construction is conditional, then reported, then ironic. This dissolves under O-09's protocol
  inversion, which stops extracting and starts requiring.

### O-11 — `effective_competence` is a feedback loop with no external anchor, and is close to inert

- **Evidence:** `effective_competence`
  (`packages/core/src/belief_ledger_core/engine/trust.py:105`) forms a Beta posterior from
  `stats.confirmed` and `stats.defeated` — the system's own defeat decisions. A source defeated by a
  mistaken belief loses competence, loses future tie-breaks, and is defeated again.
- **What bounds it, and this matters for prioritising:** `sources` is
  `episode_id NOT NULL REFERENCES episodes(id) ON DELETE CASCADE` with
  `UNIQUE(episode_id, root, kind)` (`packages/core/src/belief_ledger_core/migrations.py:78`), and `ensure_source`
  (`belief_ledger_pramana/runtime/episode_service.py:519`) constructs every source with
  `stats=SourceStats()` — zeroed. Nothing seeds stats from a prior episode. With
  `minimum_samples: 3`, the learned term only engages after three outcomes for the same source
  *inside one episode*, and resets when the episode ends.
- **Consequence:** the loop is real in structure and close to dormant in practice. Treat "the
  learned term is dead" as the null hypothesis, not "the loop is compounding".
- **Direction:** record the counterfactual before investing. In `priority_trace`, compute the
  comparison a second time with `reliability` pinned to the prior and log when the winner differs.
  It is additive and reversible. If the learned term never changes an outcome, delete it — a
  posterior that only ever returns its prior is a prior with extra steps. Freezing it is also a
  legitimate end state: a badly calibrated static prior is epistemically better than a
  self-confirming posterior, because at least you know it is a prior.
- **Note:** removing `reliability_rank` from the priority tuple was considered and rejected in
  [ADR 0010](adr/0010-scalar-competence-in-the-priority-order.md), because every contest it settles
  would become saṃśaya and `PENDING` has no drain. Freezing the scalar at its prior does not have
  that problem — the order stays total — but it changes defeat outcomes and needs its own ADR.

### O-12 — Source-defeat sampling does not distinguish evidential defeat from a configured tie-break

- **Evidence:** `_belief_transition_drafts`
  (`belief_ledger_pramana/runtime/episode_service.py:1188`) adds a source to `defeated_by_source`
  on *any* `IN -> OUT` transition, which `_relabel_summary_drafts` turns into a
  `SOURCE_STATS_DELTA` of `{"defeated": 1, "samples": 1}`. A belief that lost on the fifth
  lexicographic key counts exactly as much against its source as one contradicted by a
  PRATYAKSHA observation.
- **Why it matters:** only defeat by a different pramāṇa says anything about the world. Defeat by a
  tie-break says something about the configuration. Mixing them is what lets O-11's loop confirm
  itself.
- **Direction:** if O-11's counterfactual shows the learned term bites, split the two before doing
  anything else. If it shows the term is dead, this is moot.

### O-13 — For SHABDA, competence also moves `type_rank`

- Recorded in full as F-07 in [the obvious-fix findings register](obvious-fix-findings.md). `_type_key`
  bands testimony into `shabda_apta_hi`/`_mid`/`_lo` by `effective_competence` at the packaged
  thresholds `0.8` and `0.5`, so the scalar that is the third key also moves the second, and a
  competence gap crossing a band boundary never reaches the third key.
- **Status:** documented but undecided. The specification and
  [ADR 0010](adr/0010-scalar-competence-in-the-priority-order.md) now state the behaviour; what is
  open is whether the coupling is intended to be load-bearing or is "how good is this witness"
  expressed twice.

---

## Priority and conflict

### O-14 — `integrity_rank` dominates absolutely

- **Evidence:** `priority_trace`
  (`packages/core/src/belief_ledger_core/engine/priority.py:79`) puts `integrity_rank` first, with
  packaged values `trusted: 2`, `semi: 1`, `untrusted: 0`. A TRUSTED source at competence 0.5 beats
  a SEMI source at 0.95 at any specificity and any recency. Lexicographic order admits no
  compensation.
- **Direction, cheap:** band reliability so it cannot decide on third-decimal noise. Note this is
  half-built and already load-bearing: `reliability_bands` (`high: 0.8`, `medium: 0.5`) already
  bands SHABDA inside `type_rank`, which is the key *before* `reliability_rank` — see O-13.
  Extending banding is a smaller change than it looks.
- **Direction, correct:** stop ordering integrity and reliability against each other. Make them
  incomparable and let incomparability produce saṃśaya. "I do not know" is a better answer than an
  arbitrary winner, and it fits the rest of the design: discrete states, doubt at a tie.
- **Depends on:** O-07, without exception. Converting dominance into incomparability increases
  `PENDING` volume, and until `PENDING` has an exit this change makes the system worse. This is also
  the least certain proposal in this file: it depends on how much `PENDING` a real workload
  tolerates, which nobody has measured.

### O-15 — `PENDING` has no active exit

- **Evidence:** `packages/core/src/belief_ledger_core/engine/defeat.py` assigns `Status.PENDING`
  with cause `samsaya:` at four sites — equal priority, defeat cycle, iteration ceiling, and pending
  admission. The only route out is `complete_verification`, which O-07 shows is effectively never
  reached.
- **Status:** saṃśaya at a tie and at a cycle is the correct decision. The defect is the absence of
  a drain, which is O-07.

### O-16 — `domain_profiles` is hand-maintained configuration

- **Evidence:** `packages/core/src/belief_ledger_core/data/defaults.yaml:91` carries two entries:
  `runtime_state: {pratyaksha: 9}` and `library_internals: {shabda_official_docs: 6}`.
- **Status:** open, low severity, and the familiar maintenance cost of a small configuration DSL.
  Two entries is not yet a problem; it is recorded because the growth pattern is predictable.

---

## Architecture

### O-17 — `EpisodeService` is 2,430 lines and cannot be split by a pure move

- **Evidence:** `belief_ledger_pramana/runtime/episode_service.py` is 2,430 lines, exempt in
  `OVERSIZED_EXEMPTIONS` with a recorded ceiling. Already F-23; the same blocker applies to
  `store.py` (1,601), `api.py` (1,178) and `enforcement.py` (1,097).
- **Status:** open by decision. Splitting one class across modules needs mixins or method
  relocation, both of which change the class rather than move it. The size guard prevents further
  growth, so this is bounded debt rather than accumulating debt.

### O-18 — Duplicated surface: re-export shims and a live deprecated facade

- **Evidence:** nine modules under `belief_ledger_pramana/engine/` are re-export shims over
  `belief_ledger_core.engine`, and `LedgerRuntime`
  (`packages/core/src/belief_ledger_core/runtime.py:49`) is deprecated and still exported. Recorded
  as F-22 and F-24, with the reasoning in
  [ADR 0015](adr/0015-runtime-module-layout.md).
- **Status:** open, and deleting the shims is **not** recommended. They are the compatibility
  surface of a package whose stated purpose is backward compatibility, and they are pinned by
  `tests/fixtures/compat_surface.json`. Removing them is a contract break wearing a refactor's
  clothes.
- **Correction to a claim made in review:** `LedgerRuntime` is *not* the path the Hermes adapter
  takes. It appears only in the `core_runtime` shim, core's `__init__`, its own module, and
  `examples/deployment_gate/run.py`. `PluginRuntime` does not reference it.
- **Direction:** the real cost is cognitive load on a second contributor, and the remedy is
  documentation of which surface is current, not deletion.

---

## Security

### O-19 — Anchoring is manual; nothing publishes on a schedule

- **Evidence:** `anchor publish` exists as a CLI command and no code path calls it. A grep for
  `anchor` across `belief_ledger_pramana/runtime/` and `belief_ledger_core/api.py` returns nothing.
- **Partially addressed:** `doctor` now reports anchoring state so an unused control is visible — an
  empty `sink_path` is a notice, a configured-but-unreadable sink is a warning, a sink never
  published to is a notice, and a newest anchor disagreeing with the recomputed root is an error.
  What remains is that publishing is still an operator action nobody is reminded to schedule.
- **Direction:** publish on a schedule the operator already keeps, such as alongside backups. This
  is deployment work rather than repository work.

### O-20 — The file sink shares a host with the ledger

- **Evidence:** `FileAnchorSink`
  (`packages/core/src/belief_ledger_core/verification/anchors.py:96`) writes append-only JSONL with
  `O_APPEND` and mode `0600`, and `_validated_sink_path` refuses a path inside the ledger directory
  or a symlink. An attacker with write access to both the database and the sink defeats it.
- **Status:** open by construction and honestly documented in the module and in
  [the threat model](threat-model.md). `ChainAnchorPort` is a protocol, so a sink the local
  attacker does not control is an implementation away; the file sink is what ships.

### O-21 — The strong guarantee is not available on the audited host

- **Evidence:** the Hermes adapter caps at `accepted_final`. `HostCapabilities.missing_for(STRICT)`
  reports `atomic_action_token_consume` and the rest as missing; `strict` exists only in the
  reference runner, which is conformance evidence rather than the product.
- **Partially addressed:** `doctor` now carries a `strict_guarantee` check and emits a notice naming
  the missing capabilities, and a profile downgraded below what was requested is a warning. Before
  that both facts were report fields nothing surfaced.
- **Status:** the cap itself is not fixable here — it is a property of the host. What was fixable
  was its visibility, and that is done. Documentation must keep attributing the strong guarantee to
  the reference runner and not to the product.

---

## Project

### O-22 — No external review

- **Evidence:** `git log --format='%an' | sort -u` returns `Sergi Parpal`, `sergiparpal`, and
  `dependabot[bot]`, over 58 commits. No external contributor has read a component that decides
  security blocks.
- **Status:** open, and the hardest item here to fix with more code.
- **Direction:** the instrument proposed in O-01 is separable, dependency-free and host-neutral. A
  bench that measures the discriminating power of policy suites is adoptable by people who would
  never install a plugin for a niche agent, and `belief-ledger-core` is already neutral enough to
  publish on its own with the reference runner.

### O-23 — Coupled to one Hermes commit

- **Evidence:** `scripts/check_hermes_contract.py` pins `AUDITED_VERSION = "0.19.0"` and
  `AUDITED_COMMIT = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"`.
- **Status:** inherent to being a plugin. Mitigated by O-22's direction, not solved by it. The
  contract checker is what turns host drift into a failing build rather than a silent breakage.

---

## Tooling

### O-24 — A stale virtualenv changes the local gate's result with no warning

- **Evidence:** `scripts/verify_stage.py all` runs `python -m twine check` from the synced
  environment. A checkout whose `.venv` holds `packaging 26.0` fails that step with
  `ImportError: cannot import name 'errors' from 'packaging'`, reproducibly and on wheels unrelated
  to any change, while `uv.lock` pins `packaging 26.2` and CI passes the same job.
- **Consequence:** the documented local gate and CI can disagree for reasons that have nothing to do
  with the change under test, and nothing tells the developer which one they are looking at.
- **Direction:** have `verify_stage.py` assert the environment matches the lock before running
  anything — `uv sync --frozen --check` or equivalent — so a stale environment fails immediately and
  legibly instead of failing later as a confusing tool error.

---

## Investigated and not reproduced

Recorded so they are not raised again. Each was asserted in review and does not hold against the
code.

### N-01 — "The only thing that crosses episodes is `source.stats`"

Nothing crosses episodes. `sources` is `episode_id NOT NULL REFERENCES episodes(id) ON DELETE
CASCADE` with `UNIQUE(episode_id, root, kind)` (`packages/core/src/belief_ledger_core/migrations.py:78`), `find_source` filters by
`episode_id` (`packages/core/src/belief_ledger_core/store.py:369`), and `ensure_source`
(`belief_ledger_pramana/runtime/episode_service.py:519`) creates every source with zeroed
`SourceStats()`. The asserted inversion — rigorous typed reasoning discarded while an unverifiable
scalar accumulates — does not exist, because the scalar does not accumulate either. The real
finding, that the learned term is close to inert, is O-11.

### N-02 — "Matching the self-claim regex raises competence from 0.65 to 0.95"

It does not. `is_user_self_claim` sets `validity["about_self"]`, which selects a trust profile.
Competence is read from `candidate.domain`, which the deterministic extractor always sets to
`"general"`. The `self: 0.95` entry that made this look plausible was unreachable and has since been
removed, so the fallback through `general` now yields 0.65 for any future domain. The residual
finding about the pattern itself is O-10.

### N-03 — "`LedgerRuntime` is where the real Hermes adapter passes through"

It is not. See the correction in O-18.
