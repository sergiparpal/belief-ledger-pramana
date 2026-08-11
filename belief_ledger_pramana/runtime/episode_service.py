"""Integrated per-episode ledger service: ingestion, relabel, gate, lint and verification."""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..admission import BeliefAdmissionService
from ..application.actions import ActionEvaluationUseCase
from ..application.context import ContextCompilationUseCase
from ..application.queries import LedgerQueryService
from ..application.verification import VerificationScheduler
from ..config import (
    ConfigSnapshot,
)
from ..context.render import RenderedContext
from ..engine.contradiction import candidate_pair, candidate_tokens, classify_deterministically
from ..engine.defeat import RelabelResult
from ..engine.defeat import relabel as engine_relabel
from ..engine.graph import cycle_path
from ..engine.qualifiers import canonicalize_qualifiers
from ..engine.trust import TrustDecision, determine_admission
from ..engine.validity import normalize_content, validate_belief
from ..events import (
    EventDraft,
    canonical_json,
    content_hash,
    isoformat_utc,
    to_primitive,
    utc_now,
)
from ..gate.decision import ActionGate
from ..ids import new_id
from ..ingestion.absence import assess_negative_search
from ..ingestion.adapters import SourceDescriptor
from ..ingestion.claims import (
    ClaimCandidate,
    deterministic_candidates,
    validate_candidate,
)
from ..ingestion.provenance import fingerprint
from ..ingestion.tool import (
    prepare_evidence,
    redact_secrets,
)
from ..ingestion.user import is_user_self_claim, user_source
from ..lint.enforce import enforce_report, linter_failure_response
from ..lint.report import lint_response
from ..llm.client import HostLlmClient, LlmComponentError
from ..llm.prompts import (
    CHAIN_AUDIT,
    CLAIM_EXTRACTION,
    CONTRADICTION,
    LINT_ENTAILMENT,
    REWRITE,
)
from ..llm.schemas import (
    CHAIN_AUDIT_SCHEMA,
    CLAIM_EXTRACTION_SCHEMA,
    CONTRADICTION_SCHEMA,
    LINT_ENTAILMENT_SCHEMA,
    REWRITE_SCHEMA,
)
from ..models import (
    Belief,
    ComponentVerdict,
    Conflict,
    DefeatEdge,
    DefeatKind,
    Episode,
    Evidence,
    EvidenceRef,
    GateDecision,
    IngestionSupport,
    Integrity,
    Justification,
    LintClaim,
    LintDisposition,
    LintReport,
    Perishability,
    Pramana,
    RetractionNotice,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    Status,
    VerificationMethod,
    VerificationTask,
)
from ..store import LedgerStore
from ..verification.chain_audit import local_asiddha, validate_chain_audit
from .errors import RuntimeUnavailable, ToolEvidence
from .helpers import (
    _apply_source_profile,
    _args_hash,
    _clean,
    _contradiction_payload,
    _correlation,
    _descendant_ids,
    _explicitly_acknowledges_retraction,
    _ordered_belief_pair,
    _record_draft,
    _safe_text_hash,
    _validate_claim_result,
    _validate_contradiction,
    _validate_entailment,
    _validate_rewrite,
)

if TYPE_CHECKING:
    from .plugin_runtime import PluginRuntime

logger = logging.getLogger(__name__)


class EpisodeService:
    def __init__(
        self,
        runtime: PluginRuntime,
        episode_id: str,
        store: LedgerStore,
        config: ConfigSnapshot,
    ) -> None:
        self.runtime = runtime
        self.episode_id = episode_id
        self.store = store
        if runtime.ledger_reader is None or runtime.event_writer is None:
            raise RuntimeUnavailable("ledger ports are unavailable")
        if runtime.llm_budget_ledger is None:
            raise RuntimeUnavailable("LLM budget ledger is unavailable")
        if runtime.lifecycle is None:
            raise RuntimeUnavailable("lifecycle event recorder is unavailable")
        self.lifecycle = runtime.lifecycle
        self.snapshot = config
        self.settings = config.settings
        self.config = config.data
        self.source_profiles = runtime.source_profiles(config)
        self.scheduler = VerificationScheduler(
            runtime.ledger_reader, config, writer=runtime.event_writer
        )
        self.gate = ActionGate(
            runtime.ledger_reader,
            self.config,
            runtime.policy_registry(config),
            writer=runtime.event_writer,
        )
        self.actions = ActionEvaluationUseCase(self.gate)
        self.queries = LedgerQueryService(runtime.ledger_reader, self.config)
        self.context = ContextCompilationUseCase(
            runtime.ledger_reader,
            runtime.event_writer,
            config,
        )
        self.admission = BeliefAdmissionService(config)
        self.llm = HostLlmClient(lambda: runtime.ctx.llm, runtime.llm_budget_ledger, config)

    @property
    def episode(self) -> Episode:
        episode = self.store.get_episode(self.episode_id)
        if episode is None:
            raise RuntimeUnavailable("episode projection is unavailable")
        return episode

    def ingest_user_message(self, message: str, **kwargs: Any) -> tuple[str, ...]:
        if not message.strip():
            return ()
        descriptor = user_source(_clean(kwargs.get("sender_id")), _clean(kwargs.get("platform")))
        source = self.ensure_source(descriptor)
        storage = self.config["storage"]
        prepared = prepare_evidence(
            message,
            mode=str(storage["evidence_mode"]),
            max_excerpt_chars=int(storage["max_excerpt_chars"]),
            redact=bool(storage["redact_secrets"]),
        )
        evidence = Evidence(
            id=new_id("evidence"),
            episode_id=self.episode_id,
            kind="user_message",
            source_id=source.id,
            payload=prepared.payload,
            content_hash=prepared.full_hash,
            metadata={
                "sender_id_hash": _safe_text_hash(_clean(kwargs.get("sender_id"))),
                "channel": _clean(kwargs.get("platform")),
                "excerpt_start": prepared.excerpt_start,
                "excerpt_end": prepared.excerpt_end,
                "observed_chars": prepared.observed_chars,
            },
            observed_at=utc_now(),
            redacted=prepared.redacted,
        )
        drafts: list[EventDraft] = [
            _record_draft("EVIDENCE_INGESTED", "evidence", evidence.id, evidence)
        ]
        if prepared.redacted:
            drafts.append(
                EventDraft(
                    "EVIDENCE_REDACTED",
                    "evidence",
                    evidence.id,
                    {"reason": "secret-like material removed before persistence"},
                )
            )
        payload = prepared.payload or ""
        candidates = deterministic_candidates(
            payload,
            max_claims=int(self.config["ingestion"]["max_claims_per_evidence"]),
        )
        for candidate in candidates:
            candidate_drafts = self._candidate_drafts(
                candidate,
                evidence,
                source,
                about_self=is_user_self_claim(source, candidate.content),
            )
            drafts.extend(candidate_drafts)
        turn_id = _clean(kwargs.get("turn_id"))
        idempotency_key = (
            "user:"
            + content_hash(
                canonical_json(
                    [
                        self.episode_id,
                        turn_id,
                        prepared.full_hash,
                        "pre_llm_call",
                    ]
                )
            )
            if turn_id
            else None
        )
        events = self.store.append_events(
            self.episode_id,
            drafts,
            correlation=_correlation(kwargs),
            idempotency_key=idempotency_key,
        )
        self._after_new_beliefs()
        return tuple(event.id for event in events)

    def ingest_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        **kwargs: Any,
    ) -> tuple[str, ...]:
        tool_evidence = self._prepare_tool_evidence(tool_name, args, result, kwargs)
        drafts = self._tool_wrapper_drafts(tool_name, args, result, tool_evidence)
        if tool_evidence.prepared.redacted:
            drafts.append(
                EventDraft(
                    "EVIDENCE_REDACTED",
                    "evidence",
                    tool_evidence.evidence.id,
                    {"reason": "secret-like material removed before persistence"},
                )
            )
        if (
            tool_evidence.content_source
            and tool_evidence.prepared.payload
            and tool_evidence.adapted.content_assertive
        ):
            drafts.append(
                EventDraft(
                    "UNPROMOTED_EVIDENCE_ADDED",
                    "evidence",
                    tool_evidence.evidence.id,
                    {
                        "evidence_id": tool_evidence.evidence.id,
                        "source_profile": tool_evidence.adapted.adapter,
                        "reason": "lazy content extraction",
                    },
                )
            )
        tool_call_id = _clean(kwargs.get("tool_call_id"))
        idempotency_key = (
            "tool:"
            + content_hash(
                canonical_json(
                    [
                        self.episode_id,
                        tool_call_id,
                        "transform_tool_result",
                        tool_evidence.prepared.full_hash,
                    ]
                )
            )
            if tool_call_id
            else None
        )
        events = self.store.append_events(
            self.episode_id,
            drafts,
            correlation=_correlation(kwargs),
            idempotency_key=idempotency_key,
        )
        self.runtime.set_recent_tool_result(self.episode_id, result)
        event_ids = [event.id for event in events]
        self._promote_tool_evidence_when_ready(tool_evidence, event_ids)
        self._after_new_beliefs()
        return tuple(event_ids)

    def _prepare_tool_evidence(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        callback: Mapping[str, Any],
    ) -> ToolEvidence:
        adapted = self.runtime.adapters.adapt(
            tool_name,
            args,
            result,
            status=_clean(callback.get("status")),
            tool_call_id=_clean(callback.get("tool_call_id")),
        )
        wrapper_source = self.ensure_source(adapted.wrapper_source)
        content_source = (
            self.ensure_source(adapted.content_source) if adapted.content_source else None
        )
        storage = self.config["storage"]
        prepared = prepare_evidence(
            result,
            mode=str(storage["evidence_mode"]),
            max_excerpt_chars=int(storage["max_excerpt_chars"]),
            redact=bool(storage["redact_secrets"]),
        )
        evidence_id = new_id("evidence")
        wrapper_id = new_id("belief")
        metadata = {
            **adapted.metadata,
            "args_hash": _args_hash(args, redact=bool(storage["redact_secrets"])),
            "duration_ms": int(callback.get("duration_ms") or 0),
            "status": _clean(callback.get("status")),
            "error_type": _clean(callback.get("error_type")),
            "excerpt_start": prepared.excerpt_start,
            "excerpt_end": prepared.excerpt_end,
            "observed_chars": prepared.observed_chars,
            "wrapper_belief_id": wrapper_id,
            "content_source_id": content_source.id if content_source else "",
        }
        evidence = Evidence(
            id=evidence_id,
            episode_id=self.episode_id,
            kind="tool_result",
            source_id=wrapper_source.id,
            payload=prepared.payload,
            content_hash=prepared.full_hash,
            metadata=metadata,
            observed_at=utc_now(),
            redacted=prepared.redacted,
        )
        return ToolEvidence(adapted, wrapper_source, content_source, prepared, evidence)

    def _tool_wrapper_drafts(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        tool_evidence: ToolEvidence,
    ) -> list[EventDraft]:
        adapted = tool_evidence.adapted
        evidence = tool_evidence.evidence
        prepared = tool_evidence.prepared
        wrapper_source = tool_evidence.wrapper_source
        storage = self.config["storage"]
        wrapper_id = str(evidence.metadata["wrapper_belief_id"])
        initial = Belief(
            id=wrapper_id,
            episode_id=self.episode_id,
            content=adapted.wrapper_content,
            normalized_content=normalize_content(adapted.wrapper_content),
            pramana=Pramana.PRATYAKSHA,
            source_id=wrapper_source.id,
            evidence=(EvidenceRef(evidence.id),),
            justifications=(),
            qualifiers={"scope": "hermes tool execution"},
            perishability=Perishability.LIVE,
            observed_at=evidence.observed_at,
            stakes=self.episode.default_stakes,
            status=Status.PENDING,
            admission_status=Status.PENDING,
            domain="runtime_state",
            validity={
                "tool_ok": True,
                "parsed": adapted.parsed,
                "measured_only": True,
                "environment_integrity": True,
                "tool_reported_success": adapted.successful,
            },
        )
        wrapper = initial
        validity = validate_belief(
            wrapper,
            evidence_payloads={evidence.id: prepared.payload},
            evidence_mode=str(storage["evidence_mode"]),
            max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
            max_chars=int(self.config["ingestion"]["max_atomic_claim_chars"]),
        )
        status_override: Status | None = None
        if not validity.valid:
            # A wrapper about observing a failed/unparsed result remains valid as
            # direct evidence only when the measured-only boundary holds. Parser
            # failure itself is what was observed.
            non_parser_reasons = [reason for reason in validity.reasons if "parsed" not in reason]
            if non_parser_reasons:
                status_override = Status.OUT
        admission = self.admission.admit(
            wrapper,
            wrapper_source,
            episode_stakes=self.episode.default_stakes,
            support_evidence_id=evidence.id,
            support_validity={**wrapper.validity, "checks": validity.checks},
            status_override=status_override,
        )
        wrapper = admission.belief
        drafts: list[EventDraft] = [
            _record_draft("EVIDENCE_INGESTED", "evidence", evidence.id, evidence),
            *admission.drafts,
        ]
        drafts.extend(self._verification_drafts(wrapper, admission.trust))
        drafts.extend(
            self._typed_observation_drafts(adapted.observations, evidence, wrapper_source)
        )
        drafts.extend(
            self._absence_drafts(
                tool_name=tool_name,
                args=args,
                result=result,
                evidence=evidence,
                source=wrapper_source,
                search_succeeded=adapted.successful,
            )
        )
        return drafts

    def _typed_observation_drafts(
        self,
        observations: Sequence[str],
        evidence: Evidence,
        source: Source,
    ) -> list[EventDraft]:
        """Persist only adapter-validated, target-bound tool observations."""

        drafts: list[EventDraft] = []
        storage = self.config["storage"]
        for content in observations:
            initial = Belief(
                id=new_id("belief"),
                episode_id=self.episode_id,
                content=content,
                normalized_content=normalize_content(content),
                pramana=Pramana.PRATYAKSHA,
                source_id=source.id,
                evidence=(EvidenceRef(evidence.id),),
                justifications=(),
                qualifiers={"scope": "typed tool observation"},
                perishability=Perishability.LIVE,
                observed_at=evidence.observed_at,
                stakes=self.episode.default_stakes,
                status=Status.PENDING,
                admission_status=Status.PENDING,
                domain="runtime_state",
                validity={
                    "tool_ok": True,
                    "parsed": True,
                    "measured_only": True,
                    "environment_integrity": True,
                    "target_bound": True,
                },
            )
            validation = validate_belief(
                initial,
                evidence_payloads={evidence.id: evidence.payload},
                evidence_mode=str(storage["evidence_mode"]),
                max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
                max_chars=int(self.config["ingestion"]["max_atomic_claim_chars"]),
            )
            status_override = Status.OUT if not validation.valid else None
            admission = self.admission.admit(
                initial,
                source,
                episode_stakes=self.episode.default_stakes,
                support_evidence_id=evidence.id,
                support_validity={**initial.validity, "checks": validation.checks},
                status_override=status_override,
            )
            drafts.extend(admission.drafts)
            drafts.extend(self._verification_drafts(admission.belief, admission.trust))
        return drafts

    def _promote_tool_evidence_when_ready(
        self,
        tool_evidence: ToolEvidence,
        event_ids: list[str],
    ) -> None:
        if (
            not bool(self.config["ingestion"]["lazy_claim_extraction"])
            and tool_evidence.content_source is not None
            and tool_evidence.prepared.payload
            and tool_evidence.adapted.content_assertive
        ):
            if self.runtime.in_running_event_loop():
                self.runtime.schedule_context_maintenance(
                    self.episode_id, self.runtime.query_for(self.episode_id)
                )
            else:
                event_ids.extend(self._promote_evidence(tool_evidence.evidence.id))

    def ensure_source(self, descriptor: SourceDescriptor | None) -> Source:
        if descriptor is None:
            raise ValueError("source descriptor is required")
        descriptor = _apply_source_profile(
            descriptor,
            self.config,
            self.source_profiles,
        )
        existing = self.store.find_source(self.episode_id, descriptor.root, descriptor.kind)
        if existing:
            return existing
        source = Source(
            id=new_id("source"),
            episode_id=self.episode_id,
            kind=descriptor.kind,
            integrity=descriptor.integrity,
            name=descriptor.name,
            root=descriptor.root,
            competence=dict(descriptor.competence),
            stats=SourceStats(),
        )
        with self.runtime.episode_lock(self.episode_id):
            existing = self.store.find_source(self.episode_id, descriptor.root, descriptor.kind)
            if existing:
                return existing
            try:
                self.store.append_record(
                    self.episode_id,
                    kind="SOURCE_REGISTERED",
                    aggregate_type="source",
                    aggregate_id=source.id,
                    record=source,
                )
            except sqlite3.IntegrityError:
                concurrent = self.store.find_source(
                    self.episode_id, descriptor.root, descriptor.kind
                )
                if concurrent is None:
                    raise
                return concurrent
        return source

    def promote_relevant(self, query: str) -> tuple[str, ...]:
        items = self.store.list_unpromoted(
            self.episode_id,
            limit=int(self.config["ingestion"]["max_unpromoted_per_request"]),
        )
        if not items:
            return ()
        # Prefer evidence sharing lexical terms with the current request.
        evidence_by_id = self.store.get_evidence_many(item["evidence_id"] for item in items)
        scored: list[tuple[int, str]] = []
        query_tokens = set(normalize_content(query).split())
        for item in items:
            evidence = evidence_by_id.get(item["evidence_id"])
            score = (
                len(query_tokens & set(normalize_content(evidence.payload or "").split()))
                if evidence
                else 0
            )
            scored.append((score, item["evidence_id"]))
        scored.sort(key=lambda item: (-item[0], item[1]))
        all_event_ids: list[str] = []
        selected_ids = [
            evidence_id
            for _, evidence_id in scored[
                : int(self.config["ingestion"]["max_unpromoted_per_request"])
            ]
        ]
        source_by_id = self.store.get_sources(
            str(evidence_by_id[evidence_id].metadata.get("content_source_id", ""))
            for evidence_id in selected_ids
            if evidence_id in evidence_by_id
        )
        for evidence_id in selected_ids:
            evidence = evidence_by_id.get(evidence_id)
            content_source = (
                source_by_id.get(str(evidence.metadata.get("content_source_id", "")))
                if evidence is not None
                else None
            )
            all_event_ids.extend(
                self._promote_evidence(
                    evidence_id, evidence=evidence, content_source=content_source
                )
            )
        return tuple(all_event_ids)

    def _promote_evidence(
        self,
        evidence_id: str,
        *,
        evidence: Evidence | None = None,
        content_source: Source | None = None,
    ) -> tuple[str, ...]:
        if not self.store.is_unpromoted(self.episode_id, evidence_id):
            return ()
        evidence = evidence or self.store.get_evidence(evidence_id)
        if evidence is None or not evidence.payload:
            return ()
        content_source_id = str(evidence.metadata.get("content_source_id", ""))
        content_source = content_source or self.store.get_source(content_source_id)
        if content_source is None:
            return ()
        if content_source.id == evidence.source_id:
            events = self.store.append_events(
                self.episode_id,
                [
                    EventDraft(
                        "UNPROMOTED_EVIDENCE_FAILED",
                        "evidence",
                        evidence.id,
                        {
                            "evidence_id": evidence.id,
                            "reason": "generic execution output has no domain source",
                        },
                    )
                ],
            )
            return tuple(event.id for event in events)
        candidates, model_assisted = self._extract_claim_candidates(evidence)
        drafts, accepted_claim_count = self._claim_admission_drafts(
            candidates,
            evidence,
            content_source,
        )
        drafts.extend(
            self._promotion_outcome_drafts(evidence, accepted_claim_count, model_assisted)
        )
        drafts.extend(
            self._component_verdict_drafts(
                "claim_extractor",
                evidence.content_hash,
                "accepted" if accepted_claim_count else "inconclusive",
                {"accepted": accepted_claim_count, "model_assisted": model_assisted},
                premise_ids=(str(evidence.metadata.get("wrapper_belief_id", "")),),
            )
        )
        events = self.store.append_events(self.episode_id, drafts)
        self._after_new_beliefs()
        return tuple(event.id for event in events)

    def _extract_claim_candidates(
        self, evidence: Evidence
    ) -> tuple[tuple[ClaimCandidate, ...], bool]:
        """Prefer bounded structured extraction, with deterministic extraction as fallback."""

        payload = evidence.payload
        if payload is None:
            raise ValueError("claim extraction requires recoverable evidence")
        try:
            result = self.llm.complete_structured(
                episode_id=self.episode_id,
                purpose="belief-ledger.claim-extraction",
                instructions=CLAIM_EXTRACTION,
                text=payload,
                schema=CLAIM_EXTRACTION_SCHEMA,
                schema_name="belief_ledger_claim_extraction_v1",
                max_tokens=1_500,
                validator=lambda value: _validate_claim_result(
                    value,
                    max_claims=int(self.config["ingestion"]["max_claims_per_evidence"]),
                ),
            )
            return result.parsed, True
        except LlmComponentError:
            return (
                deterministic_candidates(
                    payload,
                    max_claims=int(self.config["ingestion"]["max_claims_per_evidence"]),
                ),
                False,
            )

    def _claim_admission_drafts(
        self,
        candidates: Sequence[ClaimCandidate],
        evidence: Evidence,
        content_source: Source,
    ) -> tuple[list[EventDraft], int]:
        drafts: list[EventDraft] = []
        accepted_claim_count = 0
        for candidate in candidates:
            validation = validate_candidate(
                candidate,
                evidence.payload or "",
                max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
                max_chars=int(self.config["ingestion"]["max_atomic_claim_chars"]),
                allowed_source_identity=content_source.name,
                allowed_pramanas={Pramana.SHABDA, Pramana.ANUPALABDHI},
            )
            if not validation.accepted:
                drafts.append(
                    EventDraft(
                        "CLAIM_REJECTED",
                        "evidence",
                        evidence.id,
                        {
                            "claim_hash": fingerprint(candidate.content),
                            "reasons": list(validation.reasons),
                        },
                    )
                )
                continue
            candidate_drafts = self._candidate_drafts(candidate, evidence, content_source)
            if any(draft.kind == "BELIEF_ADMITTED" for draft in candidate_drafts):
                accepted_claim_count += 1
            drafts.extend(candidate_drafts)
        return drafts, accepted_claim_count

    @staticmethod
    def _promotion_outcome_drafts(
        evidence: Evidence,
        accepted_claim_count: int,
        model_assisted: bool,
    ) -> list[EventDraft]:
        if accepted_claim_count:
            return [
                EventDraft(
                    "UNPROMOTED_EVIDENCE_RESOLVED",
                    "evidence",
                    evidence.id,
                    {
                        "evidence_id": evidence.id,
                        "reason": f"promoted {accepted_claim_count} validated claim(s)",
                    },
                )
            ]
        return [
            EventDraft(
                "CLAIM_EXTRACTION_FAILED",
                "evidence",
                evidence.id,
                {"reason": "no valid recoverable atomic claims", "model_assisted": model_assisted},
            ),
            EventDraft(
                "UNPROMOTED_EVIDENCE_FAILED",
                "evidence",
                evidence.id,
                {"evidence_id": evidence.id, "reason": "no valid recoverable atomic claims"},
            ),
        ]

    def record_inference(
        self,
        *,
        content: str,
        pramana: Pramana,
        premise_ids: Sequence[str],
        warrant: str,
        qualifiers: Mapping[str, str] | None = None,
        perishability: Perishability = Perishability.SLOW,
        stakes: Stakes | None = None,
        alternatives: Sequence[str] = (),
        explanandum: str = "",
        similarity_basis: str = "",
        **kwargs: Any,
    ) -> tuple[Belief, tuple[str, ...]]:
        if pramana not in {Pramana.ANUMANA, Pramana.ARTHAPATTI, Pramana.UPAMANA}:
            raise ValueError("model-authored records may only be anumana, arthapatti, or upamana")
        self.relabel()
        if not warrant.strip():
            raise ValueError("warrant must be non-empty")
        if not premise_ids:
            raise ValueError("at least one premise is required")
        premise_by_id = self.store.get_beliefs(premise_ids)
        premise_beliefs: list[Belief] = []
        for premise_id in premise_ids:
            premise = premise_by_id.get(premise_id)
            if premise is None or premise.episode_id != self.episode_id:
                raise ValueError(f"premise does not exist in episode: {premise_id}")
            if premise.status is not Status.IN:
                raise ValueError(f"premise is not IN: {premise_id}")
            premise_beliefs.append(premise)
        all_justifications = self.store.list_justifications(self.episode_id)
        belief_id = new_id("belief")
        path = cycle_path(all_justifications, belief_id, premise_ids)
        if path:
            raise ValueError("justification cycle: " + " -> ".join(path))
        model_name = _clean(kwargs.get("model")) or self.episode.model or "active-model"
        source = self.ensure_source(
            SourceDescriptor(
                SourceKind.MODEL,
                Integrity.SEMI,
                model_name,
                f"model:{model_name}",
                {"general": 0.55},
            )
        )
        justification = Justification(
            id=new_id("justification"),
            belief_id=belief_id,
            premises=tuple(premise_ids),
            warrant=warrant.strip(),
            alternatives=tuple(str(item) for item in alternatives),
        )
        validity: dict[str, Any] = {}
        if pramana is Pramana.ARTHAPATTI:
            validity.update({"explanandum": explanandum, "alternatives": list(alternatives)})
        if pramana is Pramana.UPAMANA:
            validity["similarity_basis"] = similarity_basis
        initial = Belief(
            id=belief_id,
            episode_id=self.episode_id,
            content=content.strip(),
            normalized_content=normalize_content(content),
            pramana=pramana,
            source_id=source.id,
            evidence=(),
            justifications=(justification,),
            qualifiers=canonicalize_qualifiers(qualifiers),
            perishability=perishability,
            observed_at=utc_now(),
            stakes=stakes or self.episode.default_stakes,
            status=Status.PENDING,
            admission_status=Status.PENDING,
            validity=validity,
        )
        premise_statuses = {belief.id: belief.status for belief in premise_beliefs}
        validation = validate_belief(
            initial,
            premise_statuses=premise_statuses,
            max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
            max_chars=int(self.config["ingestion"]["max_atomic_claim_chars"]),
        )
        if not validation.valid:
            raise ValueError("invalid inference: " + "; ".join(validation.reasons))
        admission = self.admission.admit(
            initial,
            source,
            episode_stakes=self.episode.default_stakes,
        )
        belief = admission.belief
        drafts = [*admission.drafts]
        drafts.extend(self._verification_drafts(belief, admission.trust))
        events = self.store.append_events(
            self.episode_id,
            drafts,
            correlation=_correlation(kwargs),
        )
        self._after_new_beliefs()
        refreshed = self.store.get_belief(belief.id) or belief
        return refreshed, tuple(event.id for event in events)

    def add_defeat(
        self,
        attacker_id: str,
        target_id: str,
        *,
        kind: DefeatKind,
        basis: str,
        reciprocal_rebut: bool = True,
    ) -> tuple[str, ...]:
        attacker = self.store.get_belief(attacker_id)
        if attacker is None or attacker.episode_id != self.episode_id:
            raise ValueError("attacker belief does not exist in this episode")
        if kind is DefeatKind.REBUT:
            target = self.store.get_belief(target_id)
            if target is None or target.episode_id != self.episode_id:
                raise ValueError("rebut target belief does not exist in this episode")
        else:
            valid_targets = {item.id for item in self.store.list_justifications(self.episode_id)}
            valid_targets.update(item.id for item in self.store.list_supports(self.episode_id))
            if target_id not in valid_targets:
                raise ValueError("undercut target is not a justification or ingestion support")
        existing = self.store.list_defeats(self.episode_id)
        pairs = {(edge.attacker, edge.target, edge.kind) for edge in existing}
        drafts: list[EventDraft] = []
        if (attacker_id, target_id, kind) not in pairs:
            edge = DefeatEdge(
                id=new_id("defeat"),
                episode_id=self.episode_id,
                attacker=attacker_id,
                target=target_id,
                kind=kind,
                basis=basis.strip() or "manual defeat",
            )
            drafts.append(_record_draft("DEFEAT_ADDED", "defeat", edge.id, edge))
        if (
            kind is DefeatKind.REBUT
            and reciprocal_rebut
            and (target_id, attacker_id, kind) not in pairs
        ):
            reverse = DefeatEdge(
                id=new_id("defeat"),
                episode_id=self.episode_id,
                attacker=target_id,
                target=attacker_id,
                kind=kind,
                basis=basis.strip() or "manual contradiction",
            )
            drafts.append(_record_draft("DEFEAT_ADDED", "defeat", reverse.id, reverse))
        if not drafts:
            return ()
        events = self.store.append_events(self.episode_id, drafts)
        transition_ids = self.relabel()
        return tuple(event.id for event in events) + transition_ids

    def request_verification(
        self,
        belief_id: str,
        method: VerificationMethod,
    ) -> tuple[VerificationTask, tuple[str, ...]]:
        belief = self.store.get_belief(belief_id)
        if belief is None or belief.episode_id != self.episode_id:
            raise ValueError("belief does not exist in this episode")
        result = self.scheduler.request(self.episode_id, belief_id, method)
        return result.task, result.event_ids

    def complete_verification(
        self, task: VerificationTask, result: str, *, cause: str
    ) -> tuple[str, ...]:
        if task.episode_id != self.episode_id or task.state != "open":
            return ()
        if result not in {"confirmed", "disconfirmed", "inconclusive"}:
            raise ValueError("invalid verification result")
        with self.runtime.episode_lock(self.episode_id):
            current = self.store.get_verification_task(task.id)
            if current is None or current.episode_id != self.episode_id or current.state != "open":
                return ()
            belief = self.store.get_belief(current.belief_id)
            if belief is None:
                raise ValueError("verification belief is missing")
            drafts: list[EventDraft] = [
                EventDraft(
                    "VERIFICATION_TASK_COMPLETED",
                    "verification_task",
                    current.id,
                    {"result": result, "state": "completed", "cause": cause},
                )
            ]
            if result in {"confirmed", "disconfirmed"}:
                new_admission = Status.IN if result == "confirmed" else Status.OUT
                if belief.admission_status is not new_admission:
                    drafts.append(
                        EventDraft(
                            "BELIEF_ADMISSION_CHANGED",
                            "belief",
                            belief.id,
                            {
                                "from": belief.admission_status.value,
                                "to": new_admission.value,
                                "cause": f"verification:{current.id}:{result}",
                            },
                        )
                    )
                source = self.store.get_source(belief.source_id)
                outcome = "confirmed" if result == "confirmed" else "defeated"
                if source and not self.store.source_outcome_sampled(belief.id, outcome):
                    drafts.append(
                        EventDraft(
                            "SOURCE_STATS_DELTA",
                            "source",
                            source.id,
                            {
                                "belief_id": belief.id,
                                "delta": {
                                    "confirmed": int(result == "confirmed"),
                                    "defeated": int(result == "disconfirmed"),
                                    "samples": 1,
                                },
                            },
                        )
                    )
            events = self.store.append_events(
                self.episode_id,
                drafts,
                require_open_verification_task_id=current.id,
            )
        if not events:
            return ()
        transition_ids = self.relabel()
        return tuple(event.id for event in events) + transition_ids

    def run_chain_audit(self, task: VerificationTask) -> tuple[str, ...]:
        belief = self.store.get_belief(task.belief_id)
        if belief is None or not belief.justifications:
            return self.complete_verification(
                task, "disconfirmed", cause="derived belief has no justification"
            )
        premise_by_id = self.store.get_beliefs(
            premise_id
            for justification in belief.justifications
            for premise_id in justification.premises
        )
        all_events: list[str] = []
        passed_any = False
        failed_causes: list[str] = []
        for justification in belief.justifications:
            statuses = {
                premise_id: premise.status
                for premise_id in justification.premises
                if (premise := premise_by_id.get(premise_id)) is not None
            }
            missing = local_asiddha(justification, statuses)
            if missing:
                failed_causes.append(f"{justification.id}:asiddha premises:" + ",".join(missing))
                continue
            payload = canonical_json(
                {
                    "belief": {"id": belief.id, "content": belief.content},
                    "premises": [
                        {
                            "id": premise_id,
                            "content": (premise_by_id.get(premise_id) or belief).content,
                        }
                        for premise_id in justification.premises
                    ],
                    "warrant": justification.warrant,
                }
            )
            try:
                result = self.llm.complete_structured(
                    episode_id=self.episode_id,
                    purpose="belief-ledger.chain-audit",
                    instructions=CHAIN_AUDIT,
                    text=payload,
                    schema=CHAIN_AUDIT_SCHEMA,
                    schema_name="belief_ledger_chain_audit_v1",
                    max_tokens=1_200,
                    validator=validate_chain_audit,
                )
            except LlmComponentError:
                return tuple(all_events)
            audit = result.parsed
            audit_passed = bool(
                not audit.fallacies
                and audit.paksadharmata
                and audit.sapakse_sattvam
                and audit.vipakse_asattvam
            )
            verdict_drafts = self._component_verdict_drafts(
                "chain_auditor",
                _safe_text_hash(payload),
                "passed" if audit_passed else "failed",
                {"fallacies": list(audit.fallacies)},
                premise_ids=justification.premises,
            )
            drafts: list[EventDraft] = [
                EventDraft(
                    "JUSTIFICATION_AUDITED",
                    "justification",
                    justification.id,
                    {"audit": to_primitive(audit)},
                ),
                *verdict_drafts,
            ]
            if not audit_passed:
                verdict_belief_id = next(
                    (
                        str(draft.payload["record"]["id"])
                        for draft in verdict_drafts
                        if draft.kind == "BELIEF_ADMITTED"
                    ),
                    "",
                )
                if verdict_belief_id:
                    edge = DefeatEdge(
                        id=new_id("defeat"),
                        episode_id=self.episode_id,
                        attacker=verdict_belief_id,
                        target=justification.id,
                        kind=DefeatKind.UNDERCUT,
                        basis=(
                            "chain audit: " + ",".join(audit.fallacies)
                            if audit.fallacies
                            else "chain audit: trairupya conditions failed"
                        ),
                    )
                    drafts.append(_record_draft("DEFEAT_ADDED", "defeat", edge.id, edge))
            events = self.store.append_events(self.episode_id, drafts)
            all_events.extend(event.id for event in events)
            if audit_passed:
                passed_any = True
            else:
                failed_causes.append(f"{justification.id}:chain audit failed")
        verification_result = "confirmed" if passed_any else "disconfirmed"
        cause = (
            "at least one trairupya chain audit passed"
            if passed_any
            else "; ".join(failed_causes) or "all registered justifications failed"
        )
        all_events.extend(self.complete_verification(task, verification_result, cause=cause))
        return tuple(all_events)

    def relabel(self) -> tuple[str, ...]:
        with self.runtime.episode_lock(self.episode_id):
            belief_list = self.store.list_beliefs(self.episode_id)
            beliefs = {belief.id: belief for belief in belief_list}
            if not beliefs:
                return ()
            sources = {source.id: source for source in self.store.list_sources(self.episode_id)}
            justifications = self.store.list_justifications(self.episode_id)
            supports = self.store.list_supports(self.episode_id)
            defeats = self.store.list_defeats(self.episode_id)
            outcome = engine_relabel(
                beliefs,
                justifications,
                supports,
                defeats,
                sources,
                self.config,
                now=utc_now(),
            )
            drafts = self._edge_activity_drafts(defeats, outcome)
            status_drafts, defeated_by_source = self._belief_transition_drafts(
                beliefs,
                justifications,
                outcome,
            )
            drafts.extend(status_drafts)
            drafts.extend(self._conflict_transition_drafts(outcome))
            drafts.extend(self._relabel_summary_drafts(defeated_by_source, outcome))
            if not drafts:
                return ()
            events = self.store.append_events(self.episode_id, drafts)
            return tuple(event.id for event in events)

    @staticmethod
    def _edge_activity_drafts(
        defeats: Sequence[DefeatEdge], outcome: RelabelResult
    ) -> list[EventDraft]:
        return [
            EventDraft("DEFEAT_ACTIVITY_CHANGED", "defeat", edge.id, {"active": active})
            for edge in defeats
            if (active := outcome.active_edges.get(edge.id, False)) != edge.active
        ]

    def _belief_transition_drafts(
        self,
        beliefs: Mapping[str, Belief],
        justifications: Sequence[Justification],
        outcome: RelabelResult,
    ) -> tuple[list[EventDraft], dict[str, list[str]]]:
        active_notices = {
            notice.defeated_belief_id
            for notice in self.store.list_retractions(self.episode_id, state="active")
        }
        newly_retracted = [
            belief_id
            for belief_id, new_status in outcome.statuses.items()
            if beliefs[belief_id].status is Status.IN and new_status is not Status.IN
        ]
        rendered_ids = self.store.rendered_belief_ids(self.episode_id, newly_retracted)
        dependents: dict[str, set[str]] = defaultdict(set)
        for justification in justifications:
            for premise_id in justification.premises:
                dependents[premise_id].add(justification.belief_id)
        current_turn = self.episode.current_turn
        ttl_turns = int(self.config["context"]["retraction_ttl_turns"])
        drafts: list[EventDraft] = []
        defeated_by_source: dict[str, list[str]] = {}
        for belief_id in sorted(outcome.statuses):
            old_belief = beliefs[belief_id]
            new_status = outcome.statuses[belief_id]
            if old_belief.status is new_status:
                continue
            cause = outcome.causes.get(belief_id, "fixed_point_relabel")
            drafts.append(
                EventDraft(
                    "BELIEF_STATUS_CHANGED",
                    "belief",
                    belief_id,
                    {"from": old_belief.status.value, "to": new_status.value, "cause": cause},
                )
            )
            if (
                old_belief.status is Status.IN
                and new_status is Status.OUT
                and not self.store.source_outcome_sampled(belief_id, "defeated")
            ):
                defeated_by_source.setdefault(old_belief.source_id, []).append(belief_id)
            if (
                old_belief.status is Status.IN
                and new_status is not Status.IN
                and belief_id in rendered_ids
                and belief_id not in active_notices
            ):
                notice = RetractionNotice(
                    id=new_id("retraction"),
                    episode_id=self.episode_id,
                    defeated_belief_id=belief_id,
                    cause=cause,
                    descendants=_descendant_ids(belief_id, dependents),
                    created_turn=current_turn,
                    ttl_turns=ttl_turns,
                )
                drafts.append(_record_draft("RETRACTION_CREATED", "retraction", notice.id, notice))
        return drafts, defeated_by_source

    def _conflict_transition_drafts(self, outcome: RelabelResult) -> list[EventDraft]:
        existing_conflicts = {
            _ordered_belief_pair(conflict.left_belief_id, conflict.right_belief_id): conflict
            for conflict in self.store.list_conflicts(self.episode_id, state="open")
        }
        new_conflicts = set(outcome.conflicts)
        open_tasks = {
            (task.belief_id, task.method): task
            for task in self.store.list_verification_tasks(self.episode_id, state="open")
        }
        drafts: list[EventDraft] = []
        for pair in sorted(new_conflicts - set(existing_conflicts)):
            task = open_tasks.get((pair[0], VerificationMethod.CROSS_SOURCE))
            if task is None:
                task = VerificationTask(
                    id=new_id("verification"),
                    episode_id=self.episode_id,
                    belief_id=pair[0],
                    method=VerificationMethod.CROSS_SOURCE,
                    k_required=1,
                    budget=1,
                )
                open_tasks[(pair[0], VerificationMethod.CROSS_SOURCE)] = task
                drafts.append(
                    _record_draft("VERIFICATION_TASK_CREATED", "verification_task", task.id, task)
                )
            conflict = Conflict(
                id=new_id("conflict"),
                episode_id=self.episode_id,
                left_belief_id=pair[0],
                right_belief_id=pair[1],
                normalized_scope={},
                verification_task_id=task.id,
            )
            drafts.append(_record_draft("CONFLICT_OPENED", "conflict", conflict.id, conflict))
        for pair in sorted(set(existing_conflicts) - new_conflicts):
            conflict = existing_conflicts[pair]
            drafts.append(
                EventDraft(
                    "CONFLICT_RESOLVED",
                    "conflict",
                    conflict.id,
                    {"reason": "fixed point no longer contains equal-priority contradiction"},
                )
            )
        return drafts

    def _relabel_summary_drafts(
        self, defeated_by_source: Mapping[str, Sequence[str]], outcome: RelabelResult
    ) -> list[EventDraft]:
        drafts = [
            EventDraft(
                "SOURCE_STATS_DELTA",
                "source",
                source_id,
                {
                    "belief_id": belief_id,
                    "delta": {"confirmed": 0, "defeated": 1, "samples": 1},
                },
            )
            for source_id, belief_ids in sorted(defeated_by_source.items())
            for belief_id in sorted(belief_ids)
        ]
        if outcome.oscillation:
            drafts.append(
                EventDraft(
                    "DEFEAT_CYCLE_SAMSAYA",
                    "episode",
                    self.episode_id,
                    {"iterations": outcome.iterations},
                )
            )
        return drafts

    def compile_context(
        self,
        *,
        query: str = "",
        request_id: str = "",
        pending_tool_intent: str = "",
        ascii_only: bool = False,
        defer_component_work: bool = False,
    ) -> RenderedContext:
        return self.context.compile(
            self.episode_id,
            self.episode,
            query=query or self.runtime.query_for(self.episode_id),
            pending_tool_intent=pending_tool_intent,
            recent_tool_result=self.runtime.recent_tool_result(self.episode_id),
            request_id=request_id,
            ascii_only=ascii_only,
            defer_component_work=defer_component_work,
            health=self.runtime.health,
            injection_failed=self.runtime.injection_failed(self.episode_id),
            defer_maintenance=lambda effective_query: self.runtime.schedule_context_maintenance(
                self.episode_id, effective_query
            ),
            promote_relevant=self.promote_relevant,
            run_chain_audit=self._run_one_relevant_chain_audit,
            relabel=self.relabel,
        )

    def run_deferred_context_maintenance(self, query: str) -> None:
        self.context.run_deferred_maintenance(
            query,
            detect_deterministic_rebuts=self._detect_deterministic_rebuts,
            relabel=self.relabel,
            complete_passive_tasks=self._complete_passive_tasks,
            promote_relevant=self.promote_relevant,
            run_chain_audit=self._run_one_relevant_chain_audit,
        )

    def lint_and_enforce(self, response: str, **kwargs: Any) -> str | None:
        stakes = self.episode.default_stakes
        marker = str(self.config["lint"]["pending_marker"])
        beliefs = self.store.list_beliefs(self.episode_id)
        input_observation = self._observe_component_input("output_linter", response)

        def relint(text: str) -> LintReport:
            return lint_response(
                text,
                self.store.list_beliefs(self.episode_id),
                pending_marker=marker,
                require_coverage=stakes in {Stakes.HIGH, Stakes.CRITICAL},
            )

        report = lint_response(
            response,
            beliefs,
            pending_marker=marker,
            require_coverage=stakes in {Stakes.HIGH, Stakes.CRITICAL},
        )
        report = self._semantic_lint(
            response,
            report,
            beliefs,
            marker,
            input_belief_id=input_observation.id,
        )

        def rewrite_once(original: str) -> str:
            active = [
                {"id": belief.id, "content": belief.content, "status": belief.status.value}
                for belief in beliefs
                if belief.status in {Status.IN, Status.PENDING}
            ]
            payload = canonical_json(
                {"response": original, "active_ledger": active, "pending_marker": marker}
            )
            result = self.llm.complete_structured(
                episode_id=self.episode_id,
                purpose="belief-ledger.output-rewrite",
                instructions=REWRITE,
                text=payload[:30_000],
                schema=REWRITE_SCHEMA,
                schema_name="belief_ledger_rewrite_v1",
                max_tokens=2_000,
                validator=_validate_rewrite,
            )
            return str(result.parsed)

        try:
            enforced = enforce_report(
                response,
                report,
                stakes=stakes,
                policy={
                    key: str(self.config["lint"][key]) for key in ("low", "med", "high", "critical")
                },
                relint=relint,
                rewrite_once=rewrite_once,
                max_rewrite_attempts=int(self.config["lint"]["max_rewrite_attempts"]),
            )
        except LlmComponentError:
            replacement = linter_failure_response(stakes, response)
            enforced = LintReport(report.claims, False, replacement, ("rewrite component failed",))
        final_text = enforced.replacement if enforced.replacement is not None else response
        verdict_drafts = self._component_verdict_drafts(
            "output_linter",
            _safe_text_hash(response),
            "passed" if enforced.passed else "failed",
            {"claims": len(enforced.claims), "warnings": list(enforced.warnings)},
            premise_ids=tuple(
                dict.fromkeys(
                    (
                        input_observation.id,
                        *(
                            belief_id
                            for claim in enforced.claims
                            for belief_id in claim.supporting_beliefs
                        ),
                    )
                )
            ),
        )
        drafts: list[EventDraft] = [
            EventDraft(
                "LINT_RECORDED",
                "response",
                _safe_text_hash(response),
                {
                    "response_hash": _safe_text_hash(response),
                    "passed": enforced.passed,
                    "report": to_primitive(enforced),
                },
            ),
            *verdict_drafts,
        ]
        for notice in self.store.list_retractions(self.episode_id):
            if _explicitly_acknowledges_retraction(final_text, notice):
                drafts.append(
                    EventDraft(
                        "RETRACTION_ACKNOWLEDGED",
                        "retraction",
                        notice.id,
                        {"response_hash": _safe_text_hash(final_text)},
                    )
                )
        self.store.append_events(
            self.episode_id,
            drafts,
            correlation=_correlation(kwargs),
        )
        return enforced.replacement

    def _semantic_lint(
        self,
        response: str,
        report: LintReport,
        beliefs: list[Belief],
        pending_marker: str,
        *,
        input_belief_id: str,
    ) -> LintReport:
        unresolved = [
            (index, claim)
            for index, claim in enumerate(report.claims)
            if claim.disposition is LintDisposition.VIKALPA
        ]
        if not unresolved:
            return report
        stop = {"the", "a", "an", "is", "are", "was", "were", "of", "to", "in"}
        candidates: dict[int, list[Belief]] = {}
        for index, claim in unresolved:
            if not claim.cited_beliefs:
                # Semantic similarity can assess a supplied citation, but it
                # must not invent one and thereby bypass the citation contract.
                continue
            claim_tokens = set(normalize_content(claim.text).split()) - stop
            ranked = sorted(
                (
                    (
                        len(claim_tokens & (set(belief.normalized_content.split()) - stop)),
                        belief,
                    )
                    for belief in beliefs
                    if belief.status in {Status.IN, Status.PENDING}
                    and belief.id in claim.cited_beliefs
                ),
                key=lambda item: (-item[0], item[1].id),
            )
            relevant = [belief for score, belief in ranked if score >= 2][:5]
            if relevant:
                candidates[index] = relevant
        if not candidates:
            return report
        payload = canonical_json(
            {
                "claims": [
                    {"index": index, "text": report.claims[index].text}
                    for index in sorted(candidates)
                ],
                "candidate_beliefs": [
                    {"claim_index": index, "id": belief.id, "content": belief.content}
                    for index in sorted(candidates)
                    for belief in candidates[index]
                ],
            }
        )
        allowed = {(index, belief.id) for index, items in candidates.items() for belief in items}
        try:
            result = self.llm.complete_structured(
                episode_id=self.episode_id,
                purpose="belief-ledger.lint-entailment",
                instructions=LINT_ENTAILMENT,
                text=payload,
                schema=LINT_ENTAILMENT_SCHEMA,
                schema_name="belief_ledger_lint_entailment_v1",
                max_tokens=900,
                validator=lambda value: _validate_entailment(value, allowed),
            )
        except LlmComponentError:
            return report
        updated = list(report.claims)
        belief_map = {belief.id: belief for belief in beliefs}
        for item in result.parsed:
            if not item["entailed"]:
                continue
            index = item["claim_index"]
            belief = belief_map[item["belief_id"]]
            original = updated[index]
            if (
                belief.status is Status.PENDING
                and pending_marker.casefold() not in original.text.casefold()
            ):
                continue
            disposition = (
                LintDisposition.PENDING_MARKED
                if belief.status is Status.PENDING
                else LintDisposition.GROUNDED
            )
            updated[index] = LintClaim(
                text=original.text,
                disposition=disposition,
                cited_beliefs=original.cited_beliefs,
                supporting_beliefs=(belief.id,),
                reason="bounded semantic entailment component matched active belief",
            )
        semantic_premises = (
            input_belief_id,
            *(belief_id for _, belief_id in sorted(allowed)),
        )
        self.store.append_events(
            self.episode_id,
            self._component_verdict_drafts(
                "lint_entailment",
                _safe_text_hash(payload),
                "completed",
                {
                    "candidate_pairs": len(allowed),
                    "entailed_pairs": sum(bool(item["entailed"]) for item in result.parsed),
                },
                premise_ids=semantic_premises,
            ),
        )
        passed = all(
            claim.disposition in {LintDisposition.GROUNDED, LintDisposition.PENDING_MARKED}
            for claim in updated
        )
        return LintReport(tuple(updated), passed)

    def _observe_component_input(self, component: str, text: str) -> Belief:
        """Record the runtime observation from which a component verdict is inferred."""

        storage = self.config["storage"]
        prepared = prepare_evidence(
            text,
            mode=str(storage["evidence_mode"]),
            max_excerpt_chars=int(storage["max_excerpt_chars"]),
            redact=bool(storage["redact_secrets"]),
        )
        input_hash = prepared.full_hash
        content = f"The {component} directly received candidate input {input_hash[:12]}"
        normalized = normalize_content(content)
        for existing in self.store.find_exact_beliefs(self.episode_id, normalized):
            if existing.status is Status.IN:
                return existing
        source = self.ensure_source(
            SourceDescriptor(
                SourceKind.TOOL,
                Integrity.TRUSTED,
                f"{component} boundary",
                f"ledger:component-input:{component}",
                {"monitoring": 1.0, "general": 1.0},
            )
        )
        evidence = Evidence(
            id=new_id("evidence"),
            episode_id=self.episode_id,
            kind="component_input",
            source_id=source.id,
            payload=prepared.payload,
            content_hash=prepared.full_hash,
            metadata={"component": component, "input_hash": input_hash},
            observed_at=utc_now(),
            redacted=prepared.redacted,
        )
        initial = Belief(
            id=new_id("belief"),
            episode_id=self.episode_id,
            content=content,
            normalized_content=normalized,
            pramana=Pramana.PRATYAKSHA,
            source_id=source.id,
            evidence=(EvidenceRef(evidence.id),),
            justifications=(),
            qualifiers={"scope": component},
            perishability=Perishability.STABLE,
            observed_at=evidence.observed_at,
            stakes=self.episode.default_stakes,
            status=Status.PENDING,
            admission_status=Status.PENDING,
            domain="monitoring",
            validity={
                "tool_ok": True,
                "parsed": True,
                "measured_only": True,
                "environment_integrity": True,
            },
        )
        admission = self.admission.admit(
            initial,
            source,
            episode_stakes=self.episode.default_stakes,
            support_evidence_id=evidence.id,
            support_validity=initial.validity,
        )
        belief = admission.belief
        events = self.store.append_events(
            self.episode_id,
            [
                _record_draft("EVIDENCE_INGESTED", "evidence", evidence.id, evidence),
                *admission.drafts,
                *self._verification_drafts(belief, admission.trust),
            ],
            idempotency_key=(
                f"component-input:{self.episode_id}:{self.episode.current_turn}:"
                f"{component}:{input_hash}"
            ),
        )
        admitted = next(
            (
                self.store.get_belief(str(event.payload["record"]["id"]))
                for event in events
                if event.kind == "BELIEF_ADMITTED"
            ),
            None,
        )
        if admitted is None:
            raise RuntimeUnavailable("component input observation was not projected")
        return admitted

    def pre_verify(self, response: str, *, attempt: int, coding: bool) -> dict[str, str] | None:
        if not coding or attempt != 0:
            return None
        marker = str(self.config["lint"]["pending_marker"])
        report = lint_response(
            response,
            self.store.list_beliefs(self.episode_id),
            pending_marker=marker,
            require_coverage=True,
        )
        if report.passed:
            return None
        missing = [
            claim.text[:160] for claim in report.claims if claim.disposition.value == "vikalpa"
        ]
        return {
            "action": "continue",
            "message": (
                "Grounding check found unsupported factual claims. Use read-only observations or "
                "registered IN premises, then cite belief IDs. Missing: " + "; ".join(missing[:5])
            ),
        }

    def record_accepted_response(self, response: str, **kwargs: Any) -> tuple[str, ...]:
        redacted, _ = (
            redact_secrets(response)
            if bool(self.config["storage"]["redact_secrets"])
            else (response, False)
        )
        event = self.store.append_events(
            self.episode_id,
            [
                EventDraft(
                    "ASSISTANT_RESPONSE_RECORDED",
                    "response",
                    _safe_text_hash(response),
                    {
                        "turn_id": _clean(kwargs.get("turn_id")),
                        "content_hash": _safe_text_hash(response),
                        "content": redacted,
                    },
                )
            ],
            correlation=_correlation(kwargs),
        )
        return tuple(item.id for item in event)

    def gate_action(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        description: str = "",
    ) -> GateDecision:
        # Freshness is a precondition of using an IN belief to authorize an
        # effectful action.  Relabel before every gate decision rather than only
        # when a context happens to be rendered.
        self.relabel()
        return self.actions.execute(self.episode_id, tool_name, args, description=description)

    def query(
        self,
        text: str,
        *,
        statuses: Sequence[Status] = (),
        pramanas: Sequence[Pramana] = (),
        limit: int = 20,
        expand_graph: bool = False,
    ) -> list[dict[str, Any]]:
        return self.queries.query(
            self.episode_id,
            text,
            statuses=statuses,
            pramanas=pramanas,
            limit=limit,
            expand_graph=expand_graph,
        )

    def explain(self, belief_id: str, *, depth: int = 4) -> dict[str, Any]:
        try:
            return self.queries.explain(self.episode_id, belief_id, depth=depth)
        except RuntimeError as exc:
            raise RuntimeUnavailable(str(exc)) from exc

    def set_stakes(self, stakes: Stakes, *, user_initiated: bool) -> tuple[str, ...]:
        current = self.episode.default_stakes
        ranks = {Stakes.LOW: 0, Stakes.MED: 1, Stakes.HIGH: 2, Stakes.CRITICAL: 3}
        if ranks[stakes] < ranks[current] and not user_initiated:
            raise ValueError("only an explicit user command may lower stakes")
        if stakes is current:
            return ()
        drafts = [
            EventDraft(
                "EPISODE_STAKES_CHANGED",
                "episode",
                self.episode_id,
                {"from": current.value, "to": stakes.value, "user_initiated": user_initiated},
            )
        ]
        beliefs = self.store.list_beliefs(self.episode_id)
        sources = {source.id: source for source in self.store.list_sources(self.episode_id)}
        open_tasks = self.store.list_verification_tasks(self.episode_id, state="open")
        tasks_by_belief: dict[str, list[VerificationTask]] = defaultdict(list)
        for task in open_tasks:
            tasks_by_belief[task.belief_id].append(task)
        for belief in beliefs:
            source = sources.get(belief.source_id)
            if source is None:
                continue
            decision = determine_admission(
                belief,
                source,
                self.config,
                episode_stakes=stakes,
            )
            if decision.status is not belief.admission_status:
                drafts.append(
                    EventDraft(
                        "BELIEF_ADMISSION_CHANGED",
                        "belief",
                        belief.id,
                        {
                            "from": belief.admission_status.value,
                            "to": decision.status.value,
                            "cause": f"episode_stakes:{current.value}->{stakes.value}",
                        },
                    )
                )
            retained_method = (
                decision.method if decision.status in {Status.PENDING, Status.QUARANTINED} else None
            )
            required_k = max(1, decision.k_required)
            retained_tasks = {
                task.id
                for task in tasks_by_belief.get(belief.id, ())
                if task.method is retained_method and task.k_required >= required_k
            }
            for task in tasks_by_belief.get(belief.id, ()):
                if task.id not in retained_tasks:
                    drafts.append(
                        EventDraft(
                            "VERIFICATION_TASK_COMPLETED",
                            "verification_task",
                            task.id,
                            {
                                "result": "superseded",
                                "state": "completed",
                                "cause": "episode stakes changed",
                            },
                        )
                    )
            if retained_method is not None and not retained_tasks:
                task = VerificationTask(
                    id=new_id("verification"),
                    episode_id=self.episode_id,
                    belief_id=belief.id,
                    method=retained_method,
                    k_required=required_k,
                    budget=required_k,
                )
                drafts.append(
                    _record_draft("VERIFICATION_TASK_CREATED", "verification_task", task.id, task)
                )
        events = self.store.append_events(self.episode_id, drafts)
        transitions = self.relabel()
        return tuple(event.id for event in events) + transitions

    def expire_retractions(self) -> tuple[str, ...]:
        turn = self.episode.current_turn
        drafts = [
            EventDraft(
                "RETRACTION_EXPIRED",
                "retraction",
                notice.id,
                {"current_turn": turn, "created_turn": notice.created_turn},
            )
            for notice in self.store.list_retractions(self.episode_id)
            if turn - notice.created_turn >= notice.ttl_turns
        ]
        if not drafts:
            return ()
        events = self.store.append_events(self.episode_id, drafts)
        return tuple(event.id for event in events)

    def _candidate_drafts(
        self,
        candidate: ClaimCandidate,
        evidence: Evidence,
        source: Source,
        *,
        about_self: bool = False,
    ) -> list[EventDraft]:
        candidate_validation = validate_candidate(
            candidate,
            evidence.payload or "",
            max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
            max_chars=int(self.config["ingestion"]["max_atomic_claim_chars"]),
            allowed_source_identity=source.name,
            allowed_pramanas={Pramana.SHABDA, Pramana.ANUPALABDHI},
        )
        if not candidate_validation.accepted:
            return [
                EventDraft(
                    "CLAIM_REJECTED",
                    "evidence",
                    evidence.id,
                    {
                        "claim_hash": fingerprint(candidate.content),
                        "reasons": list(candidate_validation.reasons),
                    },
                )
            ]
        if candidate.pramana is Pramana.ANUPALABDHI:
            return [
                EventDraft(
                    "SEARCH_FAILED",
                    "evidence",
                    evidence.id,
                    {"reason": "extractor cannot establish yogyata without search metadata"},
                )
            ]
        normalized = normalize_content(candidate.content)
        belief_id = new_id("belief")
        validity = (
            {
                "tool_ok": True,
                "parsed": True,
                "measured_only": True,
                "environment_integrity": True,
            }
            if candidate.pramana is Pramana.PRATYAKSHA
            else {
                "apta": float(
                    source.competence.get(candidate.domain, source.competence.get("general", 0.5))
                ),
                "assertive": True,
                "about_self": about_self,
            }
        )
        initial = Belief(
            id=belief_id,
            episode_id=self.episode_id,
            content=candidate.content.strip(),
            normalized_content=normalized,
            pramana=candidate.pramana,
            source_id=source.id,
            evidence=(EvidenceRef(evidence.id, (candidate.span_start, candidate.span_end)),),
            justifications=(),
            qualifiers=canonicalize_qualifiers(candidate.qualifiers),
            perishability=candidate.perishability,
            observed_at=evidence.observed_at,
            stakes=self.episode.default_stakes,
            status=Status.PENDING,
            admission_status=Status.PENDING,
            domain=candidate.domain,
            validity=validity,
        )
        validation = validate_belief(
            initial,
            evidence_payloads={evidence.id: evidence.payload},
            evidence_mode=str(self.config["storage"]["evidence_mode"]),
            max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
            max_chars=int(self.config["ingestion"]["max_atomic_claim_chars"]),
        )
        if not validation.valid:
            return [
                EventDraft(
                    "CLAIM_REJECTED",
                    "evidence",
                    evidence.id,
                    {
                        "claim_hash": fingerprint(candidate.content),
                        "reasons": list(validation.reasons),
                    },
                )
            ]
        existing_beliefs = self.store.find_exact_beliefs(self.episode_id, normalized)
        existing_sources = self.store.get_sources(
            existing.source_id for existing in existing_beliefs
        )
        for existing in existing_beliefs:
            existing_source = existing_sources.get(existing.source_id)
            if existing_source is None or existing_source.root != source.root:
                continue
            refresh_support = IngestionSupport(
                id=new_id("support"),
                episode_id=self.episode_id,
                belief_id=existing.id,
                evidence_id=evidence.id,
                validity={**validity, "checks": validation.checks, "duplicate_refresh": True},
            )
            drafts = [
                EventDraft(
                    "DUPLICATE_CONTENT_OBSERVED",
                    "belief",
                    existing.id,
                    {
                        "evidence_id": evidence.id,
                        "source_root": source.root,
                        "observed_at": isoformat_utc(evidence.observed_at),
                    },
                ),
                EventDraft(
                    "BELIEF_OBSERVATION_REFRESHED",
                    "belief",
                    existing.id,
                    {
                        "evidence_id": evidence.id,
                        "span": [candidate.span_start, candidate.span_end],
                        # `isoformat_utc`, not `datetime.isoformat`: this value is written
                        # straight into `beliefs.observed_at`, which is ordered as text
                        # alongside rows every other path stores in the trailing-Z form.
                        "observed_at": isoformat_utc(evidence.observed_at),
                    },
                ),
                _record_draft(
                    "INGESTION_SUPPORT_ADDED",
                    "ingestion_support",
                    refresh_support.id,
                    refresh_support,
                ),
            ]
            admission = self.admission.admit(
                replace(
                    existing,
                    observed_at=evidence.observed_at,
                    evidence=(
                        *existing.evidence,
                        EvidenceRef(
                            evidence.id,
                            (candidate.span_start, candidate.span_end),
                        ),
                    ),
                ),
                existing_source,
                episode_stakes=self.episode.default_stakes,
                support_evidence_id=evidence.id,
                support_validity=refresh_support.validity,
            )
            if admission.belief.admission_status is not existing.admission_status:
                drafts.append(
                    EventDraft(
                        "BELIEF_ADMISSION_CHANGED",
                        "belief",
                        existing.id,
                        {
                            "from": existing.admission_status.value,
                            "to": admission.belief.admission_status.value,
                            "cause": "fresh duplicate observation",
                        },
                    )
                )
                drafts.extend(self._verification_drafts(admission.belief, admission.trust))
            return drafts
        admission = self.admission.admit(
            initial,
            source,
            episode_stakes=self.episode.default_stakes,
            support_evidence_id=evidence.id,
            support_validity={**validity, "checks": validation.checks},
        )
        belief = admission.belief
        drafts = [*admission.drafts]
        drafts.extend(self._verification_drafts(belief, admission.trust))
        return drafts

    def _verification_drafts(self, belief: Belief, trust: TrustDecision) -> list[EventDraft]:
        if trust.method is None or trust.status not in {Status.PENDING, Status.QUARANTINED}:
            return []
        task = VerificationTask(
            id=new_id("verification"),
            episode_id=self.episode_id,
            belief_id=belief.id,
            method=trust.method,
            k_required=max(1, trust.k_required),
            budget=max(1, trust.k_required),
        )
        return [_record_draft("VERIFICATION_TASK_CREATED", "verification_task", task.id, task)]

    def _absence_drafts(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        evidence: Evidence,
        source: Source,
        search_succeeded: bool,
    ) -> list[EventDraft]:
        lowered = result.casefold().strip()
        negative = lowered in {"", "[]", "{}", "no results", "no matches", "not found"} or (
            "no results" in lowered[:300] or '"results":[]' in lowered.replace(" ", "")[:500]
        )
        if not negative or not any(
            token in tool_name.casefold() for token in ("search", "find", "grep", "retrieve")
        ):
            return []
        yogyata = self.config["trust"]["yogyata"]
        raw_parameters = args.get("parameters")
        parameters: dict[str, Any] = raw_parameters if isinstance(raw_parameters, dict) else {}
        truncated = args.get("truncated", True)
        try:
            coverage = float(args.get("coverage") or 0.0)
            recall = float(args.get("recall") or 0.0)
        except (TypeError, ValueError):
            return [
                EventDraft(
                    "SEARCH_FAILED",
                    "evidence",
                    evidence.id,
                    {"reason": "search coverage or recall is malformed", "tool_name": tool_name},
                )
            ]
        if (
            not math.isfinite(coverage)
            or not math.isfinite(recall)
            or not isinstance(truncated, bool)
        ):
            return [
                EventDraft(
                    "SEARCH_FAILED",
                    "evidence",
                    evidence.id,
                    {"reason": "search metadata is malformed", "tool_name": tool_name},
                )
            ]
        assessment = assess_negative_search(
            search_succeeded=search_succeeded,
            truncated=truncated,
            corpus=str(args.get("corpus") or ""),
            scope=str(args.get("scope") or ""),
            query=str(args.get("query") or args.get("pattern") or ""),
            parameters=parameters,
            coverage=coverage,
            recall=recall,
            min_coverage=float(yogyata["min_coverage"]),
            min_recall=float(yogyata["min_recall"]),
        )
        proposition = str(args.get("absence_proposition") or "").strip()
        if not assessment.admissible or not proposition:
            return [
                EventDraft(
                    "SEARCH_FAILED",
                    "evidence",
                    evidence.id,
                    {
                        "reason": assessment.reason
                        if assessment.reason
                        else "no world-level absence proposition was supplied",
                        "tool_name": tool_name,
                    },
                )
            ]
        belief_id = new_id("belief")
        initial = Belief(
            id=belief_id,
            episode_id=self.episode_id,
            content=proposition,
            normalized_content=normalize_content(proposition),
            pramana=Pramana.ANUPALABDHI,
            source_id=source.id,
            evidence=(EvidenceRef(evidence.id),),
            justifications=(),
            qualifiers=canonicalize_qualifiers(
                {"scope": str(args.get("scope")), "as_of": str(args.get("as_of") or "")}
            ),
            perishability=Perishability.FAST,
            observed_at=evidence.observed_at,
            stakes=self.episode.default_stakes,
            status=Status.PENDING,
            admission_status=Status.PENDING,
            domain=str(args.get("domain") or "general"),
            validity=assessment.validity,
        )
        validation = validate_belief(
            initial,
            evidence_payloads={evidence.id: evidence.payload},
            max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
            max_chars=int(self.config["ingestion"]["max_atomic_claim_chars"]),
            yogyata_min_coverage=float(yogyata["min_coverage"]),
            yogyata_min_recall=float(yogyata["min_recall"]),
        )
        if not validation.valid:
            return [
                EventDraft(
                    "SEARCH_FAILED",
                    "evidence",
                    evidence.id,
                    {"reason": "; ".join(validation.reasons), "tool_name": tool_name},
                )
            ]
        admission = self.admission.admit(
            initial,
            source,
            episode_stakes=self.episode.default_stakes,
            support_evidence_id=evidence.id,
            support_validity={**assessment.validity, "checks": validation.checks},
        )
        belief = admission.belief
        drafts = [*admission.drafts]
        drafts.extend(self._verification_drafts(belief, admission.trust))
        return drafts

    def _component_verdict_drafts(
        self,
        component: str,
        input_hash: str,
        outcome: str,
        detail: dict[str, Any],
        *,
        premise_ids: Sequence[str],
    ) -> list[EventDraft]:
        premise_by_id = self.store.get_beliefs(premise_ids)
        valid_premises = tuple(
            premise_id
            for premise_id in dict.fromkeys(premise_ids)
            if premise_id
            and (premise := premise_by_id.get(premise_id)) is not None
            and premise.status is Status.IN
        )
        verdict_id = new_id("verdict")
        verdict = ComponentVerdict(
            id=verdict_id,
            episode_id=self.episode_id,
            component=component,
            purpose=f"belief-ledger.{component}",
            input_hash=input_hash,
            outcome=outcome,
            belief_id=None,
            detail=detail,
        )
        drafts: list[EventDraft] = []
        if valid_premises:
            source = self.ensure_source(
                SourceDescriptor(
                    SourceKind.MODEL,
                    Integrity.SEMI,
                    component,
                    f"model:component:{component}",
                    {"general": 0.6},
                )
            )
            belief_id = new_id("belief")
            justification = Justification(
                id=new_id("justification"),
                belief_id=belief_id,
                premises=valid_premises,
                warrant=f"The {component} applied its versioned deterministic or structured procedure",
            )
            content = f"The {component} verdict for input {input_hash[:12]} was {outcome}"
            initial = Belief(
                id=belief_id,
                episode_id=self.episode_id,
                content=content,
                normalized_content=normalize_content(content),
                pramana=Pramana.ANUMANA,
                source_id=source.id,
                evidence=(),
                justifications=(justification,),
                qualifiers={"scope": component},
                perishability=Perishability.STABLE,
                observed_at=utc_now(),
                stakes=self.episode.default_stakes,
                status=Status.PENDING,
                admission_status=Status.PENDING,
                domain="monitoring",
                validity={"component_verdict": True},
            )
            admission = self.admission.admit(
                initial,
                source,
                episode_stakes=self.episode.default_stakes,
            )
            belief = admission.belief
            verdict = replace(verdict, belief_id=belief.id)
            drafts.extend(admission.drafts)
            drafts.extend(self._verification_drafts(belief, admission.trust))
        drafts.append(
            _record_draft("COMPONENT_VERDICT_RECORDED", "component_verdict", verdict.id, verdict)
        )
        return drafts

    def _after_new_beliefs(self) -> None:
        defer_component_work = self.runtime.in_running_event_loop()
        self._detect_deterministic_rebuts(allow_semantic=not defer_component_work)
        self.relabel()
        if self._complete_passive_tasks():
            self.relabel()
        if defer_component_work:
            self.runtime.schedule_context_maintenance(
                self.episode_id, self.runtime.query_for(self.episode_id)
            )

    def _complete_passive_tasks(self) -> tuple[str, ...]:
        tasks = self.store.list_verification_tasks(self.episode_id, state="open")
        if not tasks:
            return ()
        beliefs = self.store.list_beliefs(self.episode_id)
        belief_by_id = {belief.id: belief for belief in beliefs}
        beliefs_by_normalized: dict[str, list[Belief]] = defaultdict(list)
        for belief in beliefs:
            beliefs_by_normalized[belief.normalized_content].append(belief)
        sources = {source.id: source for source in self.store.list_sources(self.episode_id)}
        event_ids: list[str] = []
        for task in tasks:
            task_belief = belief_by_id.get(task.belief_id)
            if task_belief is None:
                continue
            if task.method is VerificationMethod.CROSS_SOURCE:
                count = self.scheduler.passive_cross_source_count(
                    task_belief, beliefs_by_normalized[task_belief.normalized_content], sources
                )
                if count >= task.k_required:
                    event_ids.extend(
                        self.complete_verification(
                            task,
                            "confirmed",
                            cause=f"{count} independently rooted matching belief(s)",
                        )
                    )
            elif task.method is VerificationMethod.TOOL_RECHECK:
                observed = [
                    candidate
                    for candidate in beliefs_by_normalized[task_belief.normalized_content]
                    if candidate.id != task_belief.id
                    and candidate.pramana is Pramana.PRATYAKSHA
                    and candidate.status is Status.IN
                    and candidate.normalized_content == task_belief.normalized_content
                ]
                if observed:
                    event_ids.extend(
                        self.complete_verification(
                            task,
                            "confirmed",
                            cause=f"tool re-observation {observed[0].id}",
                        )
                    )
        return tuple(event_ids)

    def _run_one_relevant_chain_audit(self, query: str) -> tuple[str, ...]:
        query_tokens = set(normalize_content(query).split())
        candidates: list[tuple[int, VerificationTask]] = []
        tasks = self.store.list_verification_tasks(self.episode_id, state="open")
        belief_by_id = self.store.get_beliefs(task.belief_id for task in tasks)
        for task in tasks:
            if task.method is not VerificationMethod.CHAIN_AUDIT:
                continue
            belief = belief_by_id.get(task.belief_id)
            if belief is None:
                continue
            score = len(query_tokens & set(belief.normalized_content.split()))
            candidates.append((score, task))
        if not candidates:
            return ()
        candidates.sort(key=lambda item: (-item[0], item[1].id))
        return self.run_chain_audit(candidates[0][1])

    def _detect_deterministic_rebuts(self, *, allow_semantic: bool = True) -> tuple[str, ...]:
        beliefs = self.store.list_beliefs(self.episode_id)
        defeats = self.store.list_defeats(self.episode_id)
        existing = {
            (edge.attacker, edge.target) for edge in defeats if edge.kind is DefeatKind.REBUT
        }
        token_index: dict[str, list[Belief]] = {}
        tokens_by_belief = {belief.id: candidate_tokens(belief.content) for belief in beliefs}
        drafts: list[EventDraft] = []
        considered: set[tuple[str, str]] = set()
        semantic_candidate: tuple[Belief, Belief] | None = None
        resolved_semantic_inputs = self.store.component_verdict_input_hashes(
            self.episode_id, "contradiction_classifier"
        )
        for belief in sorted(beliefs, key=lambda item: item.id):
            tokens = tokens_by_belief[belief.id]
            candidates: dict[str, Belief] = {}
            for token in tokens:
                for candidate in token_index.get(token, ()):
                    candidates[candidate.id] = candidate
            for other in sorted(candidates.values(), key=lambda item: item.id):
                pair = (belief.id, other.id) if belief.id <= other.id else (other.id, belief.id)
                if pair in considered:
                    continue
                considered.add(pair)
                if not candidate_pair(
                    belief,
                    other,
                    left_tokens=tokens,
                    right_tokens=tokens_by_belief[other.id],
                ):
                    continue
                decision = classify_deterministically(belief, other)
                if (
                    decision.outcome == "uncertain"
                    and semantic_candidate is None
                    and len(tokens & tokens_by_belief[other.id]) >= 2
                    and belief.domain not in {"runtime_state", "monitoring"}
                    and other.domain not in {"runtime_state", "monitoring"}
                    and _safe_text_hash(_contradiction_payload(belief, other))
                    not in resolved_semantic_inputs
                ):
                    semantic_candidate = (belief, other)
                if decision.outcome != "rebut":
                    continue
                for attacker, target in ((belief, other), (other, belief)):
                    if (attacker.id, target.id) in existing:
                        continue
                    edge = DefeatEdge(
                        id=new_id("defeat"),
                        episode_id=self.episode_id,
                        attacker=attacker.id,
                        target=target.id,
                        kind=DefeatKind.REBUT,
                        basis=decision.basis,
                    )
                    drafts.append(_record_draft("DEFEAT_ADDED", "defeat", edge.id, edge))
                    existing.add((attacker.id, target.id))
            for token in tokens:
                token_index.setdefault(token, []).append(belief)
        if semantic_candidate is not None and allow_semantic:
            drafts.extend(self._semantic_contradiction_drafts(*semantic_candidate, existing))
        if not drafts:
            return ()
        events = self.store.append_events(self.episode_id, drafts)
        return tuple(event.id for event in events)

    def _semantic_contradiction_drafts(
        self,
        left: Belief,
        right: Belief,
        existing: set[tuple[str, str]],
    ) -> list[EventDraft]:
        payload = _contradiction_payload(left, right)
        try:
            result = self.llm.complete_structured(
                episode_id=self.episode_id,
                purpose="belief-ledger.contradiction",
                instructions=CONTRADICTION,
                text=payload,
                schema=CONTRADICTION_SCHEMA,
                schema_name="belief_ledger_contradiction_v1",
                max_tokens=600,
                validator=_validate_contradiction,
            )
        except LlmComponentError:
            return self._component_verdict_drafts(
                "contradiction_classifier",
                _safe_text_hash(payload),
                "unavailable",
                {"left": left.id, "right": right.id, "reason": "component call failed"},
                premise_ids=(left.id, right.id),
            )
        outcome = result.parsed
        drafts = self._component_verdict_drafts(
            "contradiction_classifier",
            _safe_text_hash(payload),
            outcome["outcome"],
            {"basis": outcome["basis"], "left": left.id, "right": right.id},
            premise_ids=(left.id, right.id),
        )
        if outcome["outcome"] == "rebut":
            for attacker, target in ((left, right), (right, left)):
                if (attacker.id, target.id) in existing:
                    continue
                edge = DefeatEdge(
                    id=new_id("defeat"),
                    episode_id=self.episode_id,
                    attacker=attacker.id,
                    target=target.id,
                    kind=DefeatKind.REBUT,
                    basis=str(outcome["basis"]),
                )
                drafts.append(_record_draft("DEFEAT_ADDED", "defeat", edge.id, edge))
                existing.add((attacker.id, target.id))
        elif outcome["outcome"] == "uncertain":
            already = any(
                task.belief_id == left.id and task.method is VerificationMethod.CROSS_SOURCE
                for task in self.store.list_verification_tasks(self.episode_id, state="open")
            )
            if not already:
                task = VerificationTask(
                    id=new_id("verification"),
                    episode_id=self.episode_id,
                    belief_id=left.id,
                    method=VerificationMethod.CROSS_SOURCE,
                    k_required=1,
                    budget=1,
                )
                drafts.append(
                    _record_draft("VERIFICATION_TASK_CREATED", "verification_task", task.id, task)
                )
        return drafts
