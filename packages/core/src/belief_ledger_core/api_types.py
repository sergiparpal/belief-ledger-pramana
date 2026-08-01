"""Immutable request and result values for the public service API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enforcement import ActionBinding
from .immutable import freeze
from .models import GateDecision


class BeliefLedgerError(RuntimeError):
    """Typed API failure carrying a stable machine-readable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class EpisodeHandle:
    schema_version: int
    id: str
    state: str


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    """A normalized observation; provenance trust is assigned by the owning adapter."""

    schema_version: int
    content: str
    source_name: str
    source_kind: str
    source_integrity: str
    provenance_root: str
    observed_at: datetime | None = None
    kind: str = "direct_observation"
    subject: str = ""
    target: str = ""
    correlation: tuple[tuple[str, str], ...] = ()
    retention_mode: str = "excerpt"
    pramana: str = "pratyaksha"
    stakes: str = "med"
    qualifiers: tuple[tuple[str, str], ...] = ()
    derived_from: tuple[str, ...] = ()
    belief_content: str | None = None
    perishability: str = "stable"
    validity: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "validity",
            tuple((str(key), freeze(value)) for key, value in self.validity),
        )

    @classmethod
    def normalize(
        cls,
        content: object,
        *,
        source_name: object,
        source_kind: object = "tool",
        source_integrity: object = "semi",
        provenance_root: object | None = None,
        observed_at: datetime | None = None,
        kind: object = "direct_observation",
        subject: object = "",
        target: object = "",
        correlation: Mapping[str, object] | None = None,
        retention_mode: object = "excerpt",
        pramana: object = "pratyaksha",
        stakes: object = "med",
        qualifiers: Mapping[str, object] | None = None,
        derived_from: tuple[str, ...] = (),
        belief_content: object | None = None,
        perishability: object = "stable",
        validity: Mapping[str, object] | None = None,
    ) -> EvidenceObservation:
        if source_name is None or content is None:
            raise BeliefLedgerError(
                "INVALID_EVIDENCE_OBSERVATION",
                "observation content and source_name must be non-empty",
            )
        name = str(source_name).strip()
        text = str(content).strip()
        if not name or not text:
            raise BeliefLedgerError(
                "INVALID_EVIDENCE_OBSERVATION",
                "observation content and source_name must be non-empty",
            )
        root = str(provenance_root or name).strip()
        return cls(
            1,
            text,
            name,
            str(source_kind).strip().casefold(),
            str(source_integrity).strip().casefold(),
            root,
            observed_at,
            str(kind).strip() or "direct_observation",
            str(subject).strip(),
            str(target).strip(),
            tuple(sorted((str(key), str(value)) for key, value in (correlation or {}).items())),
            str(retention_mode).strip().casefold(),
            str(pramana).strip().casefold(),
            str(stakes).strip().casefold(),
            tuple(sorted((str(key), str(value)) for key, value in (qualifiers or {}).items())),
            tuple(str(item) for item in derived_from),
            str(belief_content).strip() if belief_content is not None else None,
            str(perishability).strip().casefold(),
            tuple(sorted((str(key), value) for key, value in (validity or {}).items())),
        )


@dataclass(frozen=True, slots=True)
class EvidenceAdmission:
    schema_version: int
    evidence_id: str
    belief_id: str
    source_id: str
    status: str
    reason_code: str
    redacted: bool


@dataclass(frozen=True, slots=True)
class ActionPermit:
    """Opaque in-process permit. Only ``consume_permission`` accepts this value."""

    schema_version: int
    decision_id: str
    binding: ActionBinding
    expires_at: str
    _raw_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ActionAuthorization:
    schema_version: int
    decision: GateDecision
    decision_id: str
    permit: ActionPermit | None
    supporting_belief_ids: tuple[str, ...]
    blocking_conflict_ids: tuple[str, ...]
    policy_digest: str
    configuration_digest: str

    @property
    def reason_code(self) -> str:
        return self.decision.reason_code

    @property
    def outcome(self) -> str:
        return self.decision.outcome.value


@dataclass(frozen=True, slots=True)
class PermissionConsumption:
    schema_version: int
    consumed: bool
    reason_code: str
    decision_id: str


@dataclass(frozen=True, slots=True)
class OutputEvaluation:
    schema_version: int
    accepted: bool
    reason_code: str
    lint_report_id: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class DecisionExplanation:
    schema_version: int
    decision_id: str
    decision: dict[str, Any]
    policy: dict[str, Any] | None
    supports: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    approval_binding: dict[str, Any] | None
    validity: str
    transitions: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ChainVerification:
    schema_version: int
    valid: bool
    reason_code: str
    projection_hashes: tuple[tuple[str, str], ...]
