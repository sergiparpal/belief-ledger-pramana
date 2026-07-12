# Typed Belief Ledger (pramāṇa) — Specification v0.1-draft

**What it is:** harness middleware that maintains, for each agent episode, a ledger of beliefs typed by epistemic source, with a justification graph, a defeat engine (bādha), and an explicit trust policy (svataḥ/parataḥ). It integrates with the agent loop at five points and produces the epistemic context block seen by the model.

**Technical lineage:** JTMS (Doyle 1979) for reason maintenance, Pollock's defeaters (rebutting/undercutting) for defeat semantics, and AGM as a revision reference; pramāṇas provide the provenance scheme, and the classical validity conditions (āpta, vyāpti, yogyatā) are operationalized as checks.

---

## 0. Scope and non-goals

**Goals.** (1) Every factual assertion the agent uses or emits is traceable to a belief with a type, provenance, and state. (2) The arrival of contradictory evidence produces structural, propagated retraction rather than rhetorical correction. (3) The decision to trust by default versus verify first is explicit configuration by source × stakes, not a prompt accident.

**Non-goals (v0.x).**
- **This is not an anti-injection defense.** Channel integrity is marked at ingestion and affects priorities, but defense against instructions embedded in content is an orthogonal harness layer. The ledger assumes it; it does not implement it.
- **This is not a probabilistic reasoner.** A belief's state is discrete (IN/OUT/PENDING/QUARANTINED); scalar confidence exists as an auxiliary field but does not govern defeat. The rationale is in §4.
- **This is not a knowledge graph.** No ontology or logical form is required: beliefs are normalized natural-language propositions. Structure lives in the justification and defeat graph, not in the content.
- **This is not long-term memory.** The ledger is per episode (or per task). Its interaction with persistent memory is defined by rule R6; everything else belongs to the vāsanā-store project.

---

## 1. Design principles (non-negotiable rules)

**R1 — Discrete state, structural defeat.** The operational question is not “how probable is P?” but “is P currently supported, by what chain, and what would defeat it?” Discrete states make retraction computable and auditable (transitions have a recorded cause). Probability can be added on top; the reverse does not work: a logprob does not tell you what to retract when a defeater arrives.

**R2 — Wrapper/content.** Reading is not knowing. A tool's direct observation covers exactly what that tool measures. A page fetch produces *two* beliefs of different types: pratyakṣa (“URL U returned 200 with a body containing T,” source = the tool itself) and śabda (“what T asserts,” source = U's domain, with its āpta). Confusing them is the number-one typing error in RAG systems and the entry point for half of “cited” hallucinations.

**R3 — Absence has a validity condition (yogyatā).** “Not found” is evidence of absence only if the searcher would have found the object had it been present. Every anupalabdhi belief must include an estimate of detectability (corpus coverage for that query class × the retriever's estimated recall, with recorded search parameters). If the condition is not met, the result is recorded as a `SEARCH_FAILED` event (evidence about the search), never as a belief about the world.

**R4 — Memory is not a pramāṇa.** A belief retrieved from an earlier episode is not new knowledge: it re-enters with its original type, temporal qualifiers, and a perishability discount; it must pass its type's validity conditions again. Memory is transport, not a source.

**R5 — The monitor is content.** The claim extractor, linter, and verifiers are themselves fallible processes whose verdicts are recorded as anumāna beliefs with source = that component; they are auditable and have their own statistics. No system component has privileged-witness status.

**R6 — Testimony independence.** For corroboration and parataḥ(k) verification, two testimonies count as independent only if their provenance roots differ (different domain/origin) and their content is not near-duplicate (similarity-based deduplication). N chunks from the same mirror are one witness, not N.

**R7 — Qualifiers before contradiction.** Before declaring REBUT, scopes are normalized: “X as of 2024” and “X as of 2026” do not contradict each other; “according to the official documentation” and “according to observed behavior” can coexist when marked. Many apparent contradictions are scope mismatches; the detector must reconcile qualifiers first.

**R8 — Event sourcing.** Everything is append-only: beliefs are materialized views over an event log. Auditability is not a feature; it is the storage format.

---

## 2. Data model

### 2.1 Entities

```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

class SourceKind(str, Enum):
    TOOL = "tool"            # executors: shell, python, http, fs...
    RETRIEVER = "retriever"  # in-house RAG indexes
    WEB = "web"
    DOCUMENT = "document"    # documents supplied to the episode
    USER = "user"
    MODEL = "model"          # the LLM itself or other agents/components
    LEDGER = "ledger"        # re-ingestion from prior episodes (R4)

class Integrity(str, Enum):
    TRUSTED = "trusted"      # channel controlled by the operator
    SEMI = "semi"            # third party with an established reputation
    UNTRUSTED = "untrusted"  # open web / injectable content

@dataclass
class Source:
    id: str
    kind: SourceKind
    integrity: Integrity
    competence: dict[str, float]      # domain -> [0,1]; editable prior (āpta)
    stats: "SourceStats"              # confirmations, received defeats, n
    # operational āpta = competence[domain] modulated by stats (§5.4)

class Pramana(str, Enum):
    PRATYAKSHA = "pratyaksha"    # direct tool observation
    SHABDA = "shabda"            # testimony (content asserted by a source)
    ANUMANA = "anumana"          # model inference
    ARTHAPATTI = "arthapatti"    # abduction / best explanation
    UPAMANA = "upamana"          # analogy (optional in v0.x)
    ANUPALABDHI = "anupalabdhi"  # absence, subject to yogyatā (R3)

class Status(str, Enum):
    IN = "in"                    # supported and usable
    OUT = "out"                  # defeated or unsupported
    PENDING = "pending"          # parataḥ: awaiting verification
    QUARANTINED = "quarantined"  # untrusted, unsanitized channel

class Perishability(str, Enum):
    STABLE = "stable"   # mathematics, frozen APIs, history
    SLOW = "slow"       # library documentation, organization charts
    FAST = "fast"       # versions, prices, service states
    LIVE = "live"       # runtime state, files, processes

class Stakes(str, Enum):
    LOW = "low"; MED = "med"; HIGH = "high"; CRITICAL = "critical"

@dataclass
class EvidenceRef:
    evidence_id: str
    span: tuple[int, int] | None = None   # offsets into the payload

@dataclass
class Justification:
    id: str
    premises: list[str]          # belief_ids; must be IN to be live
    warrant: str                 # the vyāpti: general rule invoked, in natural language
    audit: "ChainAudit | None"   # result of the trairūpya checklist (Appendix A)

@dataclass
class Belief:
    id: str
    content: str                       # atomic, self-contained proposition (§2.2)
    pramana: Pramana
    source_id: str
    evidence: list[EvidenceRef]        # required for PRATYAKSHA/SHABDA
    justifications: list[Justification]  # required for ANUMANA/ARTHAPATTI
    qualifiers: dict[str, str]         # {"as_of": ..., "scope": ..., "assumes": ...}
    perishability: Perishability
    observed_at: datetime
    stakes: Stakes                     # inherited from the task; escalatable by action
    status: Status
    confidence: float | None = None    # auxiliary; does NOT govern bādha (R1)
    corroboration: int = 0             # number of agreeing independent sources (R6)

@dataclass
class DefeatEdge:
    id: str
    attacker: str                      # belief_id
    target: str                        # belief_id (REBUT) | justification_id (UNDERCUT)
    kind: str                          # "REBUT" | "UNDERCUT"
    basis: str                         # natural-language explanation (audit)
    active: bool                       # recalculated by the engine (§4)

@dataclass
class VerificationTask:
    id: str
    belief_id: str
    method: str        # cross_source | tool_recheck | chain_audit | human
    k_required: int    # required independent confirmations
    budget: int        # allocated tokens/calls
    result: str | None # confirmed | disconfirmed | inconclusive
```

### 2.2 Belief-content rules

Rendering, contradiction detection, and claim matching depend on disciplined `content`:

1. **Atomic:** one proposition per belief. If ingestion produces a conjunction, split it.
2. **Self-contained:** no pronouns or deictics (“this version,” “the prior file”); entities use their full names.
3. **Time and scope explicit** where applicable, in `qualifiers` rather than ambiguous prose.
4. **Target length ≤ ~40 words.** More usually indicates a lack of atomicity.
5. **Deduplication:** hash normalized content; near-duplicates from the *same* provenance root are merged (they do not add corroboration); those from independent roots increase `corroboration` (R6).

### 2.3 Persistence schema (sketch)

```sql
CREATE TABLE events (              -- source of truth, append-only (R8)
  seq INTEGER PRIMARY KEY,
  ts TEXT, kind TEXT,              -- INGESTED|TYPED|ADMITTED|DEFEATED|REINSTATED|
  payload JSON                     -- VERIFIED|RETRACTION_NOTICED|GATE_BLOCKED|SEARCH_FAILED
);
CREATE TABLE evidence  (id TEXT PRIMARY KEY, kind TEXT, payload_ref TEXT,
                        content_hash TEXT, meta JSON, ts TEXT);
CREATE TABLE sources   (id TEXT PRIMARY KEY, kind TEXT, integrity TEXT,
                        competence JSON, stats JSON);
CREATE TABLE beliefs   (id TEXT PRIMARY KEY, content TEXT, pramana TEXT,
                        source_id TEXT, qualifiers JSON, perishability TEXT,
                        observed_at TEXT, stakes TEXT, status TEXT,
                        confidence REAL, corroboration INTEGER);
CREATE TABLE belief_evidence (belief_id TEXT, evidence_id TEXT, span JSON);
CREATE TABLE justifications  (id TEXT PRIMARY KEY, belief_id TEXT,
                              warrant TEXT, audit JSON);
CREATE TABLE justification_premises (justification_id TEXT, premise_belief_id TEXT);
CREATE TABLE defeats   (id TEXT PRIMARY KEY, attacker TEXT, target TEXT,
                        kind TEXT, basis TEXT, active INTEGER);
CREATE TABLE verification_tasks (id TEXT PRIMARY KEY, belief_id TEXT, method TEXT,
                                 k_required INTEGER, budget INTEGER, result TEXT);
```

An optional vector index on `beliefs.content` supports the compiler's relevance selection (§6.1). SQLite is enough for v0.x; the graph fits in memory per episode (hundreds to a few thousand nodes).

---

## 3. Type registry: validity conditions and typical defeaters

| Type | What produces it | Validity condition at ingestion | Typical defeaters |
|---|---|---|---|
| **PRATYAKSHA** | Output from a harness-executed tool | The tool completed OK; output was parsed; the belief covers only what was measured (R2); the environment is intact | UNDERCUT: incorrectly invoked tool, corrupt environment, demonstrated flakiness. REBUT: subsequent re-observation for FAST/LIVE facts |
| **SHABDA** | Content asserted by a document, website, user, or other agent | Required citation to an evidence span; source with computable āpta; marked channel (Integrity) | REBUT: pratyakṣa or testimony with higher āpta. UNDERCUT: source discredited in the domain, obsolete document, satirical/non-assertive context |
| **ANUMANA** | A conclusion derived by the model | Premises are listed and all IN; explicit warrant (vyāpti); optional audit (Appendix A) according to stakes | UNDERCUT: hetvābhāsa detected in the chain, a premise falls to OUT. REBUT: contradictory belief with higher priority |
| **ARTHAPATTI** | Abductive postulation (“it is explained only if…”) | The explanandum is IN; considered and rejected alternatives are recorded | UNDERCUT: a viable alternative appears (the type's constitutive defeater) |
| **UPAMANA** | Analogy (“API B behaves like A”) | Explicit similarity basis; always marked as the lowest priority | UNDERCUT: a relevant disanalogy is identified. *Optional in v0.x: it can be modeled as anumāna with an analogical warrant* |
| **ANUPALABDHI** | Negative search | **yogyatā** (R3): coverage(query_class, corpus) ≥ θ and estimated_recall ≥ θ′, with recorded search parameters | REBUT: any later positive finding (always wins). UNDERCUT: insufficient coverage is demonstrated |

Cross-cutting rules:

- **User:** assertions about themselves or their preferences → śabda with high āpta by default; assertions about the world → śabda with domain āpta.
- **Other agent/LLM:** always śabda with source = that model/component; it never inherits the types of sources that agent says it used (unless it exports its own signed ledger, a future extension).
- **Prior ledger (memory):** re-enters with original type + `qualifiers.as_of` + perishability discount (R4). LIVE facts never re-enter as IN: re-observe them.
- **vikalpa is not a type:** it is the linter's verdict on output spans with no supporting IN belief (§7.3).

---

## 4. Defeat engine (bādha)

### 4.1 Semantics

Two attack types (Pollock):

- **REBUT** (`attacker ⟂ target`): the propositions contradict each other after normalizing qualifiers (R7). It attacks the *belief*.
- **UNDERCUT**: attacks a *justification* or the ingestion validity of a basic belief (the evidence→belief link), without asserting its negation. It is the computational form of khyāti theories: it explains how a convincing cognition can arise from a defective process.

REBUT detection: neighborhood blocking (same entity/topic cluster via embeddings) avoids O(n²); an NLI/LLM contradiction check then runs over candidate pairs, followed by qualifier reconciliation. Only pairs that survive generate a DefeatEdge.

### 4.2 Priority order

```
priority(b) = ( integrity_rank(source(b)),      # trusted=2 > semi=1 > untrusted=0
                type_rank(b.pramana, domain),   # configurable table; see YAML
                reliability(b),                  # effective āpta or chain quality, discretized
                specificity(b),                  # specific > general (lex specialis)
                recency_rank(b) )                # matters only if perishability ∈ {FAST, LIVE}
```

Lexicographic comparison. A REBUT **wins** when `priority(attacker) > priority(target)` strictly at the first component that differs. Equality or configured incomparability → **saṃśaya**: neither defeats the other; both are marked CONFLICT, rendered as an open conflict (§6.2), and a VerificationTask is emitted. Conflicts are not silently resolved: doubt triggers inquiry, not an arbitrary tie-break.

```yaml
type_rank:
  default:            {pratyaksha: 5, shabda_apta_hi: 4, anumana_audited: 4,
                       shabda_apta_mid: 3, anumana_raw: 2, arthapatti: 2,
                       upamana: 1, shabda_apta_lo: 1}
domain_profiles:
  runtime_state:      {pratyaksha: 9}     # observed system state is authoritative
  library_internals:  {shabda_official_docs: 6}  # official docs > local inference
fixed_rules:
  - "positive finding > anupalabdhi, always"
  - "QUARANTINED/untrusted never defeats trusted, regardless of type"
```

### 4.3 Relabeling (fixed point, JTMS style)

The justification graph is kept acyclic on write (a cycle is rejected and must be reformulated). Defeat edges may form cycles; they use the saṃśaya rule.

```python
def relabel(ledger):
    # 0) normalize qualifiers; recompute current REBUT pairs (R7)
    # 1) initialization: basics with type validity OK -> IN candidates;
    #    derived beliefs -> unknown
    # 2) iterate to a fixed point:
    #    live(j)          = all(status(p) == IN for p in j.premises) \
    #                       and not active_undercut(j)
    #    support(b)       = valid_basic(b) or any(live(j) for j in b.justifications)
    #    winning_rebut(b) = exists a: rebuts(a, b) and status(a) == IN \
    #                       and priority(a) > priority(b)
    #    status(b)        = IN  if support(b) and not winning_rebut(b)
    #                       OUT if not support(b) or winning_rebut(b)
    # 3) unresolved mutual defeats (a⟂b, tied priorities or odd cycle):
    #    both -> PENDING + VerificationTask(saṃśaya)
    # 4) emit events for every transition (retractions §4.4, āpta §5.4)
```

Termination: finite lattice + the PENDING rule for cycles; in practice, use an iteration ceiling with an alarm. **Reinstatement** is free: if an attacker falls to OUT on a later pass, its target recovers IN in the same fixed point.

### 4.4 Retraction protocol

Defeat alone is not enough: the model may already have used the belief. When a belief previously *rendered in context* transitions IN→OUT:

1. Queue `RetractionNotice(belief, cause, affected_descendants)`.
2. The compiler renders it in the RETRACTIONS block (§6.2) during subsequent turns, until the model produces output that does not depend on it (as verified by the linter) or a TTL expires.
3. Descendants (anumāna that used it as a premise) fall by propagation and are listed with it: retraction is of the subtree, not the node.

This protocol is the system's core: it turns “the model corrects itself if it remembers” into “the harness ensures that the correction arrives and propagates.”

---

## 5. Trust policy (prāmāṇya)

### 5.1 Modes

- **svataḥ** — admit as IN immediately; defeasible (intrinsic validity with defeaters).
- **parataḥ(k, method)** — enter as PENDING; become IN after k confirmations by the specified method (validity requiring external certification).
- **quarantine** — do not enter the active graph; visible only in audit.

### 5.2 Source × stakes matrix (defaults; YAML configuration)

| Source \ Stakes | LOW | MED | HIGH | CRITICAL |
|---|---|---|---|---|
| pratyakṣa (own tool) | svataḥ | svataḥ | svataḥ | parataḥ(1, re-observation) |
| TRUSTED internal śabda | svataḥ | svataḥ | parataḥ(1) | parataḥ(2) |
| SEMI web śabda | svataḥ | parataḥ(1) | parataḥ(2, independent) | parataḥ(2, independent + tool) |
| UNTRUSTED web śabda | svataḥ* | parataḥ(1) | parataḥ(2, independent) | quarantine until corroborated |
| user (about self) | svataḥ | svataḥ | svataḥ | confirm in chat |
| user (about the world) | svataḥ | svataḥ | parataḥ(1) | parataḥ(2) |
| anumāna (recorded chain) | svataḥ | svataḥ | parataḥ(chain_audit) | parataḥ(audit + tool_recheck) |
| anupalabdhi | yogyatā | yogyatā | yogyatā + re-search | do not admit: require a positive finding |

\* svataḥ but with `integrity_rank = 0`: usable for low-cost tasks, incapable of defeating anything trusted.

The **stakes** are declared by the task (default MED), and the **action escalates them**: if a belief is a precondition of a HIGH action (§7.4), its effective cell is HIGH even if the task was LOW.

### 5.3 Verifiers

- `cross_source`: k independent testimonies under R6 (different provenance roots + semantic deduplication).
- `tool_recheck`: turn śabda into pratyakṣa when an observable exists (“the documentation says X → run it and see”). This is the preferred verification: it raises the type, not just a counter.
- `chain_audit`: trairūpya checklist + hetvābhāsa linter over the justification (Appendix A).
- `human`: escalation; blocks at CRITICAL if there is no response.

**Budget:** every episode carries a verification budget (calls/tokens). PENDING beliefs that exhaust their budget remain PENDING; the compiler renders them with an explicit qualifier (“according to F, unverified”), and the linter permits citations only with that marker. This is honest degradation, not silent degradation.

### 5.4 āpta learning

A slow loop over `Source.stats`: each confirmed defeat of one of the source's beliefs decrements its effective domain competence; each independent confirmation increments it (with Beta-style smoothing and floor/ceiling). This is the learned “parataḥ-aprāmāṇya” component: reliability is gained and lost through history, not declared once. Adjustments are auditable events like everything else.

---

## 6. Context compiler

The ledger is inert if the prompt does not make the model honor it. The compiler is the real product.

### 6.1 Selection

1. Retrieve beliefs relevant to the current step (vector index over `content` + graph expansion: if an anumāna enters, the IDs of its premises enter too).
2. Sort by (mandatory, priority, relevance) under a token budget. Mandatory: live retractions > open conflicts > preconditions of the current action > everything else.
3. Render PENDING only when directly relevant, always with its marker.

### 6.2 Rendering (line grammar)

```
[<id>][<type>][<meta>] <content> <qualifiers>

type:  P = pratyakṣa · Ś = śabda · A = anumāna · Ap = arthāpatti · ¬∃ = anupalabdhi
meta:  P  -> tool and timestamp       Ś  -> source and effective ā=āpta
       A  -> ← premises [+audit✓]     ¬∃ -> yogyatā✓(θ) and query
```

Example compiled block:

```
### LEDGER — relevant active beliefs
[b41][P][pip index versions foo · 14:02] foo: latest stable version = 2.4.1
[b17][Ś][foo.dev ā=0.6] foo 2.x requires Python >= 3.10  {as_of: 2026-05} (UNVERIFIED)
[b52][A ← b41,b09 · audit✓][warrant: semver, patch changes do not break the public API]
      the signature of foo.bar() is identical between 2.4.0 and 2.4.1
[b60][¬∃][yogyatā✓ 0.92 · grep -rn "legacy_mode" src/] no use of legacy_mode exists in src/

### OPEN CONFLICTS (saṃśaya)
b17 ⟂ b33 (the internal README says Python >= 3.9) — verification vt-7 is under way. Do not assume either.

### RETRACTIONS
b12 “foo: latest stable version = 2.3” — DEFEATED by b41 (pratyakṣa > web śabda).
b29 also falls (the plan to pin 2.3, derived from b12). Correct any step that depends on them.

### GENERATION CONTRACT
- Every factual assertion in your output must cite [b·] or be preceded by "speculation:".
- Do not cite OUT or QUARANTINED beliefs; cite PENDING beliefs only with "(unverified)".
- If you need a fact that is not in the ledger, say so and propose how to obtain it (tool/search).
```

### 6.3 Compression

In long episodes, ledger summaries preserve the typing (they summarize by subgraph, retaining the IDs and types of root nodes). Collapsing to prose destroys exactly the structure that justifies the system; it is prohibited by design.

---

## 7. Ingestion, linting, and action gate

### 7.1 Tool-result ingestion

Each `ToolResult` produces: an immutable EvidenceObject (hash, metadata), a wrapper pratyakṣa belief (always), and zero or more content śabda beliefs if the payload contains assertions (R2). The assertion extractor is an inexpensive LLM with an extraction prompt and the §2.2 rules; its cost is controlled through on-demand (lazy) extraction: content remains indexed as evidence and is promoted to śabda beliefs only when relevance selection reaches it.

UNTRUSTED-channel content: assertions are typed with `integrity=untrusted`; any instruction-shaped text is not ingested as either a belief or a command (an anti-injection harness layer, out of scope but assumed).

### 7.2 User-message and self-generation ingestion

- **User:** claims are extracted lazily under the cross-cutting rules in §3.
- **Model generation:** conclusions the model declares (“therefore X”) are recorded as anumāna *only if* the contract was met (cited premises). Otherwise, they are vikalpa candidates (§7.3), not beliefs.

### 7.3 Linter (vikalpa)

Over the turn's final output (and optionally over intermediate actions at HIGH+):

1. Extract declarative factual assertions from the output.
2. Match every assertion against the ledger for entailment by an IN belief (or a PENDING belief when it carries the marker).
3. Classify: **grounded** (valid citation) · **inferible** (record the missing anumāna with its premises) · **vikalpa** (unsupported).
4. Apply the stakes policy: LOW → annotate and continue; MED → rewrite with a speculation marker or seek grounding; HIGH+ → block the output until resolved.

The linter is content (R5): its verdicts are recorded with source = linter, and its precision is measured against a labeled set (§10). Without that measurement, the linter is an unaudited oracle—the very thing this system exists to eliminate.

### 7.4 Action gate (preconditions)

Before executing an effectful action (with HIGH/CRITICAL stakes declared in the tool schema):

```python
def gate_action(action) -> "ALLOW | ASK | BLOCK":
    for p in preconditions(action):      # declared in the tool schema or inferred
        b = ledger.entails(p)
        if b is None or b.status != Status.IN or priority(b) < min_priority(action.stakes):
            return ASK(missing=p, suggestion=how_to_obtain(p))
    return ALLOW
```

This is the inexpensive intervention point: between evaluation and commitment, before the effect propagates. Typical preconditions (“the file exists,” “the user confirmed,” “the environment is staging”) are exactly the kind of beliefs this system maintains well.

---

## 8. Harness integration

### 8.1 Interface (Protocol)

```python
class LedgerMiddleware(Protocol):
    def on_user_message(self, msg: Message) -> None: ...
    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None: ...
    def compile_context(self, state: TaskState, budget_tokens: int) -> str: ...
    def on_model_output(self, text: str, actions: list[ToolCall]) -> LintReport: ...
    def gate_action(self, action: ToolCall) -> GateDecision: ...

# asynchronous workers:
#   verifier_worker(queue[VerificationTask])   -> runs §5.3, emits VERIFIED
#   apta_updater(events)                        -> §5.4
#   relabel is invoked after every graph mutation (cheap: affected subgraph)
```

### 8.2 Adapter for your harness

This specification makes no assumptions about Hermes internals; the adapter is about 50 lines if the harness exposes (a) a tool-call wrapper, (b) message middleware, and (c) a hook or wrapper around the model client for pre/post-generation. If (c) is absent, wrap the client. The compiler injects its block as an ephemeral system message per turn (non-accumulative: it is regenerated; history lives in the ledger, not the transcript).

Call order per turn:

```
user_msg → on_user_message → compile_context → LLM → on_model_output
  → [gate_action → tool → on_tool_result → relabel]* → compile_context → LLM → ...
```

---

## 9. End-to-end example trace

Task (MED stakes): “update the requirements and code for the latest version of foo.”

1. The retriever returns a blog post: `e1`. Ingestion: `b12 = [Ś][blog.example ā=0.5] “latest stable version of foo = 2.3” {as_of: 2025-11}`. The §5.2 matrix (SEMI web × MED) → parataḥ(1) … the budget permits it → vt-1 (tool_recheck) is queued; meanwhile, it remains PENDING.
2. With b12 rendered as PENDING, the model tentatively proposes `b29 = [A ← b12] “pin foo==2.3”` — the compiler admits it with a citation and “(unverified).”
3. vt-1 runs `pip index versions foo` → `e2`, `b41 = [P] “latest stable version of foo = 2.4.1”`. The contradiction detector finds b41 ⟂ b12 (qualifiers reconciled: both claim to apply *now*; b12 is also FAST and old). `priority(b41) > priority(b12)` in type_rank → winning REBUT.
4. `relabel`: b12 → OUT; b29 loses its only live justification → OUT. DEFEATED×2 events; RetractionNotice(b12, {b29}). `apta_updater`: blog.example loses competence in `python_packaging`.
5. On the next turn, the compiler renders b41 IN and RETRACTIONS with b12+b29. The model corrects the plan, citing [b41]. The linter confirms that output no longer depends on b12 → the notice expires.
6. Before writing requirements (a HIGH action under the schema), the gate requires “requirements.txt exists in the repository” → no belief exists → ASK → the harness runs `ls`, creates the pratyakṣa, ALLOW.

What this system buys in this trace: the correction did not depend on the model “remembering” the blog post; it was structural, propagated to the descendant, auditable, and left the source with a damaged reputation.

---

## 10. Evaluation and collapse criteria

### Suites

- **A — Grounding QA:** time-sensitive, multi-hop QA with distractors. Metrics: vikalpa rate in final answers (independent grader), citation precision/coverage, and calibration of “unverified” markers.
- **B — bādha probes (the project's own suite):** synthetic episodes with scheduled arrival of contradictory evidence and ground truth for what should win. Metrics: retraction rate, propagation completeness (% of relabeled descendants), wrong-winner rate, and turns-to-retract. There is almost no public benchmark for belief revision in agents; this suite is itself a publishable artifact.
- **C — Agentic tasks with injected failures:** obsolete documentation versus runtime state, and absences with and without yogyatā. Metrics: task success, rate of unsafe actions blocked by the gate, and false blocks.
- **D — The linter itself (R5):** precision/recall of the vikalpa detector against a manually labeled set (~300 claims). Without D, A is not interpretable.

### Ablations

Flat baseline · types only (no defeat) · defeat only (no types) · no compiler contract · no gate. The question for every ablation: which component earns its cost?

### Costs and collapse

Record token and call overhead by configuration. The collapse criterion, inherited from the original document: if Suite A does not improve materially (provisional proposal: ≥15% relative reduction in vikalpa rate) with acceptable overhead (≤ +35% tokens at MED), collapse to flat context and retain only what the ablations preserve. The numbers are placeholders to calibrate in v0.2; the commitment to an abandonment criterion is not.

---

## 11. Roadmap

- **v0.1 (1–2 weeks):** schema + events + compiler with rendering and contract; manual/semi-automatic ingestion; manual defeat. Objective: cheaply falsify the minimum hypothesis—does typed rendering alone move Suite A?
- **v0.2 (+2–3 weeks):** automatic ingestion with the wrapper/content rule; contradiction detection (blocking + NLI); bādha with fixed priorities; retraction protocol; Suite B v1.
- **v0.3 (+3–4 weeks):** vikalpa linter over final outputs; budgeted parataḥ verifiers; āpta learning; Suite D.
- **v1.0:** action gate with preconditions in tool schemas; complete suites + ablations; freeze the specification and decide on collapse or continuation.

## 12. Open problems and honest risks

1. **Claim granularity.** Atomicity is fuzzy; over-atomizing explodes the graph, while under-atomizing breaks selective defeat. Mitigation: the §2.2 rules + a per-episode belief budget; it is empirical.
2. **The extractor and linter are LLMs.** Their errors create false vikalpa alarms (fatigue) or false grounded judgments (worse). That is why Suite D is a prerequisite for any conclusion, and why R5 is not decorative.
3. **Contract adherence.** Whether the model reliably cites [b·] varies by model and is empirical prompt engineering. The fallback is honest: if it does not cite, the linter treats the assertion as vikalpa and applies policy.
4. **Cost.** Extraction + NLI + verification can require 1.5–3× calls if everything is eager. Existing mitigations in the specification are lazy extraction, blocking, linting only final outputs outside HIGH, and explicit budgets.
5. **Manually configured priorities.** The §4.2 order is a reasonable initial configuration, not truth. Learning it from bādha-probe results (which order minimizes wrong winners) is the natural extension.
6. **Bayesianization.** A future variant uses soft states with thresholds. It is deliberately deferred (R1); if the probes show that discrete lexicography loses decisive information, this is the first place to yield.
7. **Multi-agent.** Exchange of signed subgraphs between ledgers (testimony with an attached chain) is outside v0.x, but the typing already prepares for it: another agent is a śabda source with its own āpta.

---

## Appendix A — Chain audit (for `chain_audit`)

**Trairūpya checklist** over the justification (warrant + premises):

1. *pakṣadharmatā* — the reason genuinely applies to the present case (the premises mention this case, not a similar one).
2. *sapakṣe sattvam* — at least one positive instance of the warrant exists (a recordable concrete example; tradition required the udāharaṇa in the syllogism itself).
3. *vipakṣe asattvam* — a quick search for a counterexample fails (n attempts by the model itself or by the critic).

**Hetvābhāsa taxonomy as lint categories** (connected to project no. 2, the chain linter):

| Category | Modern reading | Engine action |
|---|---|---|
| savyabhicāra (inconclusive) | the warrant permits known counterexamples | UNDERCUT the justification |
| viruddha (contradictory) | the warrant, correctly applied, supports the negation | UNDERCUT + alert |
| satpratipakṣa (counterbalanced) | an opposing chain of equal priority exists | mark CONFLICT (saṃśaya) |
| asiddha (premise not established) | some premise is not IN | standard propagation already covers it |
| bādhita (defeated by a superior) | conclusion contradicted by a higher-priority belief | it is literally the §4 REBUT; the linter only labels it |

## Appendix B — Concept → component traceability

| Concept | Specification component |
|---|---|
| pramāṇa (source typology) | `Pramana` enum + §3 registry |
| āpta (competent and honest source) | `Source.competence/integrity` + §5.4 learning |
| vyāpti / trairūpya | `Justification.warrant` + `chain_audit` (App. A) |
| anupalabdhi + yogyatā | ANUPALABDHI type + R3 |
| bādha / khyāti | §4 engine: REBUT / UNDERCUT |
| svataḥ vs. parataḥ-prāmāṇya | §5.2 matrix |
| saṃśaya (doubt triggers inquiry) | CONFLICT → VerificationTask §4.2 |
| vikalpa | §7.3 linter verdict |
| smṛti is not a pramāṇa | R4 (memory as transport) |
| vedanā as an intervention point | §7.4 action gate |
| the witness is not privileged | R5 (monitor as content) + Suite D |

*End of specification v0.1-draft.*
