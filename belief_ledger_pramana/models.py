"""Immutable domain records for the typed belief ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


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
