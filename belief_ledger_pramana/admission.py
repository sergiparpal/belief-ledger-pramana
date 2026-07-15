"""Shared construction of admitted beliefs and their ingestion support events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .engine.trust import TrustDecision, determine_admission
from .events import to_primitive
from .ids import new_id
from .models import Belief, IngestionSupport, Source, Stakes, Status
from .store import EventDraft


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """A belief after policy admission and its corresponding event drafts."""

    belief: Belief
    trust: TrustDecision
    drafts: tuple[EventDraft, ...]


class BeliefAdmissionService:
    """Apply trust admission consistently after a caller has validated a belief."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def admit(
        self,
        initial: Belief,
        source: Source,
        *,
        episode_stakes: Stakes,
        support_evidence_id: str | None = None,
        support_validity: Mapping[str, Any] | None = None,
        status_override: Status | None = None,
    ) -> AdmissionResult:
        """Create the common admission/support records for a validated belief."""

        trust = determine_admission(
            initial,
            source,
            self._config,
            episode_stakes=episode_stakes,
        )
        admitted_status = status_override or trust.status
        belief = replace(initial, status=admitted_status, admission_status=admitted_status)
        drafts = [_record_draft("BELIEF_ADMITTED", "belief", belief.id, belief)]
        if support_evidence_id is not None:
            support = IngestionSupport(
                id=new_id("support"),
                episode_id=belief.episode_id,
                belief_id=belief.id,
                evidence_id=support_evidence_id,
                validity=dict(support_validity or belief.validity),
            )
            drafts.append(_record_draft("INGESTION_SUPPORT_ADDED", "ingestion_support", support.id, support))
        return AdmissionResult(belief, trust, tuple(drafts))


def _record_draft(kind: str, aggregate_type: str, aggregate_id: str, record: Any) -> EventDraft:
    return EventDraft(kind, aggregate_type, aggregate_id, {"record": to_primitive(record)})
