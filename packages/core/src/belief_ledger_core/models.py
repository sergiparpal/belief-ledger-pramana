"""Immutable domain records for the typed belief ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .immutable import freeze


class SourceKind(StrEnum):
    TOOL = "tool"
    RETRIEVER = "retriever"
    WEB = "web"
    DOCUMENT = "document"
    USER = "user"
    MODEL = "model"
    LEDGER = "ledger"


class Integrity(StrEnum):
    TRUSTED = "trusted"
    SEMI = "semi"
    UNTRUSTED = "untrusted"


class Pramana(StrEnum):
    PRATYAKSHA = "pratyaksha"
    SHABDA = "shabda"
    ANUMANA = "anumana"
    ARTHAPATTI = "arthapatti"
    UPAMANA = "upamana"
    ANUPALABDHI = "anupalabdhi"


class Status(StrEnum):
    IN = "in"
    OUT = "out"
    PENDING = "pending"
    QUARANTINED = "quarantined"


class Perishability(StrEnum):
    STABLE = "stable"
    SLOW = "slow"
    FAST = "fast"
    LIVE = "live"


class Stakes(StrEnum):
    LOW = "low"
    MED = "med"
    HIGH = "high"
    CRITICAL = "critical"


class DefeatKind(StrEnum):
    REBUT = "REBUT"
    UNDERCUT = "UNDERCUT"


class VerificationMethod(StrEnum):
    CROSS_SOURCE = "cross_source"
    TOOL_RECHECK = "tool_recheck"
    CHAIN_AUDIT = "chain_audit"
    HUMAN = "human"


class Health(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class CompatibilityMode(StrEnum):
    FULL = "full"
    HOOK_CONTEXT = "hook_context"
    DIAGNOSTICS_ONLY = "diagnostics_only"


class GateOutcome(StrEnum):
    ALLOW = "allow"
    APPROVE = "approve"
    BLOCK = "block"


class LintDisposition(StrEnum):
    GROUNDED = "grounded"
    INFERIBLE = "inferible"
    PENDING_MARKED = "pending_marked"
    VIKALPA = "vikalpa"


@dataclass(frozen=True, slots=True)
class SourceStats:
    confirmed: int = 0
    defeated: int = 0
    samples: int = 0


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    episode_id: str
    kind: SourceKind
    integrity: Integrity
    name: str
    root: str
    competence: dict[str, float] = field(default_factory=dict)
    stats: SourceStats = field(default_factory=SourceStats)

    def __post_init__(self) -> None:
        object.__setattr__(self, "competence", freeze(self.competence))


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    span: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    episode_id: str
    kind: str
    source_id: str
    payload: str | None
    content_hash: str
    metadata: dict[str, Any]
    observed_at: datetime
    redacted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class ChainAudit:
    paksadharmata: bool
    sapakse_sattvam: bool
    vipakse_asattvam: bool
    evidence_ids: tuple[str, ...] = ()
    fallacies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Justification:
    id: str
    belief_id: str
    premises: tuple[str, ...]
    warrant: str
    audit: ChainAudit | None = None
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Belief:
    id: str
    episode_id: str
    content: str
    normalized_content: str
    pramana: Pramana
    source_id: str
    evidence: tuple[EvidenceRef, ...]
    justifications: tuple[Justification, ...]
    qualifiers: dict[str, str]
    perishability: Perishability
    observed_at: datetime
    stakes: Stakes
    status: Status
    admission_status: Status
    domain: str = "general"
    confidence: float | None = None
    corroboration: int = 0
    validity: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Timezone awareness is guaranteed here rather than where observed_at is read. Since
        # recency became an unconditional priority key (ADR 0011), every belief is compared by
        # timestamp, so a naive value would fail deep inside defeat resolution instead of at the
        # boundary that admitted it. parse_datetime and FixedClock enforce the same rule.
        if self.observed_at.tzinfo is None:
            raise ValueError("belief observed_at must be timezone-aware")
        object.__setattr__(self, "qualifiers", freeze(self.qualifiers))
        object.__setattr__(self, "validity", freeze(self.validity))


@dataclass(frozen=True, slots=True)
class DefeatEdge:
    id: str
    episode_id: str
    attacker: str
    target: str
    kind: DefeatKind
    basis: str
    active: bool = False


@dataclass(frozen=True, slots=True)
class VerificationTask:
    id: str
    episode_id: str
    belief_id: str
    method: VerificationMethod
    k_required: int
    budget: int
    result: str | None = None
    state: str = "open"


@dataclass(frozen=True, slots=True)
class IngestionSupport:
    id: str
    episode_id: str
    belief_id: str
    evidence_id: str
    validity: dict[str, Any]
    active: bool = True


@dataclass(frozen=True, slots=True)
class Conflict:
    id: str
    episode_id: str
    left_belief_id: str
    right_belief_id: str
    normalized_scope: dict[str, str]
    verification_task_id: str
    state: str = "open"

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_scope", freeze(self.normalized_scope))


@dataclass(frozen=True, slots=True)
class RetractionNotice:
    id: str
    episode_id: str
    defeated_belief_id: str
    cause: str
    descendants: tuple[str, ...]
    created_turn: int
    ttl_turns: int
    state: str = "active"


@dataclass(frozen=True, slots=True)
class RenderedBelief:
    episode_id: str
    belief_id: str
    request_id: str
    turn_number: int
    rendered_at: datetime


@dataclass(frozen=True, slots=True)
class ComponentVerdict:
    id: str
    episode_id: str
    component: str
    purpose: str
    input_hash: str
    outcome: str
    belief_id: str | None
    detail: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", freeze(self.detail))


@dataclass(frozen=True, slots=True)
class LlmUsage:
    id: str
    episode_id: str
    purpose: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float | None
    latency_ms: int
    turn_number: int
    outcome: str


@dataclass(frozen=True, slots=True)
class LlmCallAttribution:
    """Everything needed to detect that one input produced two different outputs.

    Written as a sibling of `ComponentVerdict` rather than as fields on it. `ComponentVerdict` and
    `LlmUsage` are frozen v1 record kinds, and adding a required field to either would move hashes
    that `tests/fixtures/v1_replay/` pins. `LLM_CALL_ATTRIBUTION` appears in no v1 fixture, so it
    is hash-neutral by construction (ADR 0012).

    `prompt_hash` digests the instruction text itself. The prompt module describes itself as
    versioned but carries no version constant, and inventing a parallel numbering scheme would
    create a second thing to keep in step. A digest of the prompt cannot drift from the prompt.

    `input_hash` and `output_hash` are computed over redacted, canonicalised content with the same
    `content_hash` helper the event chain uses, so neither commits to a credential.
    """

    id: str
    episode_id: str
    purpose: str
    provider: str
    model: str
    prompt_hash: str
    input_hash: str
    output_hash: str | None
    sampling: dict[str, Any]
    outcome: str
    turn_number: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "sampling", freeze(self.sampling))


@dataclass(frozen=True, slots=True)
class Episode:
    id: str
    key: str
    session_id: str
    task_id: str
    platform: str
    model: str
    default_stakes: Stakes
    current_turn: int
    created_at: datetime
    updated_at: datetime
    compatibility_mode: CompatibilityMode
    llm_calls_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    state: str = "active"


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    id: str
    episode_id: str
    timestamp: datetime
    kind: str
    schema_version: int
    aggregate_type: str
    aggregate_id: str
    correlation: dict[str, str]
    causal_event_id: str | None
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    auth_tag: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "correlation", freeze(self.correlation))
        object.__setattr__(self, "payload", freeze(self.payload))


@dataclass(frozen=True, slots=True)
class GateDecision:
    outcome: GateOutcome
    reason_code: str
    message: str
    stakes: Stakes
    missing: tuple[str, ...] = ()
    suggested_observation: str | None = None
    rule_key: str | None = None


@dataclass(frozen=True, slots=True)
class LintClaim:
    text: str
    disposition: LintDisposition
    cited_beliefs: tuple[str, ...] = ()
    supporting_beliefs: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LintReport:
    claims: tuple[LintClaim, ...]
    passed: bool
    replacement: str | None = None
    warnings: tuple[str, ...] = ()


STAKE_RANK: dict[Stakes, int] = {
    Stakes.LOW: 0,
    Stakes.MED: 1,
    Stakes.HIGH: 2,
    Stakes.CRITICAL: 3,
}


def max_stakes(*values: Stakes) -> Stakes:
    """Return the highest effective stakes value."""

    if not values:
        return Stakes.MED
    return max(values, key=STAKE_RANK.__getitem__)
