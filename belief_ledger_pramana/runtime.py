"""Lazy episode registry and integrated ledger service container."""

from __future__ import annotations

import contextvars
import json
import logging
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .compatibility import CompatibilityReport, inspect_host
from .config import (
    ConfigError,
    ConfigSnapshot,
    StatePaths,
    config_needs_reload,
    load_config,
    packaged_yaml,
    state_paths,
)
from .context.inject import HermesRequestInjector
from .context.render import RenderedContext, render_context
from .context.select import select_beliefs
from .engine.contradiction import candidate_pair, classify_deterministically
from .engine.defeat import relabel as engine_relabel
from .engine.graph import cycle_path
from .engine.priority import priority_trace
from .engine.qualifiers import canonicalize_qualifiers
from .engine.trust import TrustDecision, determine_admission
from .engine.validity import normalize_content, validate_belief
from .events import canonical_json, content_hash, to_primitive, utc_now
from .gate.classify import ActionPolicyRegistry
from .gate.decision import ActionGate
from .ids import new_id
from .ingestion.absence import assess_negative_search
from .ingestion.adapters import SourceDescriptor, ToolAdapterRegistry
from .ingestion.claims import (
    ClaimCandidate,
    candidate_from_structured,
    deterministic_candidates,
    validate_candidate,
)
from .ingestion.provenance import fingerprint
from .ingestion.tool import prepare_evidence, redact_secrets
from .ingestion.user import is_about_user_self, user_source
from .lint.enforce import enforce_report, linter_failure_response
from .lint.report import lint_response
from .llm.client import HostLlmClient, LlmComponentError
from .llm.prompts import (
    CHAIN_AUDIT,
    CLAIM_EXTRACTION,
    CONTRADICTION,
    LINT_ENTAILMENT,
    REWRITE,
)
from .llm.schemas import (
    CHAIN_AUDIT_SCHEMA,
    CLAIM_EXTRACTION_SCHEMA,
    CONTRADICTION_SCHEMA,
    LINT_ENTAILMENT_SCHEMA,
    REWRITE_SCHEMA,
)
from .models import (
    Belief,
    CompatibilityMode,
    ComponentVerdict,
    Conflict,
    DefeatEdge,
    DefeatKind,
    Episode,
    Evidence,
    EvidenceRef,
    GateDecision,
    Health,
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
from .store import EventDraft, LedgerStore
from .verification.chain_audit import local_asiddha, validate_chain_audit
from .verification.scheduler import VerificationScheduler

logger = logging.getLogger(__name__)


class RuntimeUnavailable(RuntimeError):
    pass


class EpisodeResolutionError(ValueError):
    pass


class PluginRuntime:
    """Process-local registry; durable truth remains in the event store."""

    def __init__(
        self,
        ctx: Any,
        *,
        compatibility: CompatibilityReport | None = None,
        hermes_home: Path | None = None,
    ) -> None:
        self.ctx = ctx
        self.compatibility = compatibility or inspect_host(ctx)
        self.hermes_home = hermes_home
        self.injector = HermesRequestInjector()
        self.adapters = ToolAdapterRegistry()
        self._initialize_lock = threading.RLock()
        self._registry_lock = threading.RLock()
        self._episode_locks: dict[str, threading.RLock] = {}
        self._turn_to_episode: dict[str, str] = {}
        self._approval_to_episode: dict[str, str] = {}
        self._begun_turns: set[tuple[str, str]] = set()
        self._turn_configs: dict[str, ConfigSnapshot] = {}
        self._queries: dict[str, str] = {}
        self._recent_tool_results: dict[str, str] = {}
        self._injection_failures: set[str] = set()
        self._current_episode: contextvars.ContextVar[str] = contextvars.ContextVar(
            "belief_ledger_current_episode", default=""
        )
        self._config: ConfigSnapshot | None = None
        self.paths: StatePaths | None = None
        self.store: LedgerStore | None = None
        self.health = Health.HEALTHY
        self.health_reasons: list[str] = []
        self.transform_callback: Any | None = None
        self.loaded_module_path: str | None = None
        self.manifest_source: str | None = None

    @property
    def initialized(self) -> bool:
        return self.store is not None and self._config is not None

    def ensure_initialized(self) -> None:
        if self.initialized:
            return
        with self._initialize_lock:
            if self.initialized:
                return
            try:
                snapshot, paths = load_config(hermes_home=self.hermes_home)
            except ConfigError as exc:
                self.health = Health.DEGRADED
                self.health_reasons.append(f"invalid configuration: {exc}")
                # Safety fallback remains enforcing and is always reported; it is
                # used only so doctor/export can access diagnostics.
                defaults = packaged_yaml("defaults.yaml")
                snapshot = ConfigSnapshot(
                    defaults,
                    None,
                    (str(exc),),
                    content_hash(canonical_json(defaults)),
                    None,
                )
                paths = state_paths(self.hermes_home)
                paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                store = LedgerStore(
                    paths.database,
                    busy_timeout_ms=int(snapshot.data["storage"]["busy_timeout_ms"]),
                )
                store.verify_hash_chain()
            except Exception as exc:
                self.health = Health.UNAVAILABLE
                self.health_reasons.append(f"database unavailable: {type(exc).__name__}: {exc}")
                raise RuntimeUnavailable(self.health_reasons[-1]) from exc
            self._config = snapshot
            self.paths = paths
            self.store = store
            if self.compatibility.mode is not CompatibilityMode.FULL:
                self.health = Health.DEGRADED
                self.health_reasons.extend(self.compatibility.errors or self.compatibility.warnings)

    @property
    def config(self) -> ConfigSnapshot:
        self.ensure_initialized()
        assert self._config is not None
        return self._config

    def operational(self) -> bool:
        return self.compatibility.mode in {CompatibilityMode.FULL, CompatibilityMode.HOOK_CONTEXT}

    def begin_turn(self, **kwargs: Any) -> EpisodeService:
        service = self.service(**kwargs)
        turn_id = _clean(kwargs.get("turn_id"))
        session_id = _clean(kwargs.get("session_id"))
        if turn_id:
            with self._registry_lock:
                self._turn_to_episode[turn_id] = service.episode_id
        marker = turn_id or f"implicit:{content_hash(_clean(kwargs.get('user_message')))[:16]}"
        key = (service.episode_id, marker)
        with self._registry_lock:
            first = key not in self._begun_turns
            if first:
                self._begun_turns.add(key)
        if first:
            self._reload_at_boundary()
            assert self.store is not None
            episode = self.store.get_episode(service.episode_id)
            if episode is None:
                raise RuntimeUnavailable("episode disappeared during turn initialization")
            now = utc_now()
            self.store.append_events(
                service.episode_id,
                [
                    EventDraft(
                        "EPISODE_TURN_STARTED",
                        "episode",
                        service.episode_id,
                        {"current_turn": episode.current_turn + 1, "updated_at": now},
                    )
                ],
                correlation=_correlation(kwargs),
                idempotency_key=f"turn:{service.episode_id}:{marker}",
            )
            with self._registry_lock:
                self._turn_configs[service.episode_id] = self.config
        if session_id and turn_id:
            with self._registry_lock:
                self._turn_to_episode[turn_id] = service.episode_id
        query = _clean(kwargs.get("user_message"))
        if query:
            self._queries[service.episode_id] = query
        current = self.service_for_id(service.episode_id)
        current.expire_retractions()
        return current

    def service(self, **kwargs: Any) -> EpisodeService:
        self.ensure_initialized()
        episode_id = self.resolve_episode_id(**kwargs)
        return self.service_for_id(episode_id)

    def service_for_id(self, episode_id: str) -> EpisodeService:
        self.ensure_initialized()
        assert self.store is not None
        snapshot = self._turn_configs.get(episode_id, self.config)
        self._current_episode.set(episode_id)
        return EpisodeService(self, episode_id, self.store, snapshot)

    def current_service(self) -> EpisodeService:
        episode_id = self._current_episode.get()
        if episode_id and self.store is not None and self.store.get_episode(episode_id):
            return self.service_for_id(episode_id)
        self.ensure_initialized()
        assert self.store is not None
        active = [episode for episode in self.store.list_episodes() if episode.state == "active"]
        if not active:
            raise EpisodeResolutionError("no active ledger episode")
        return self.service_for_id(active[0].id)

    def resolve_episode_id(self, **kwargs: Any) -> str:
        self.ensure_initialized()
        assert self.store is not None
        session_id = _clean(kwargs.get("session_id"))
        session_key = _clean(kwargs.get("session_key"))
        turn_id = _clean(kwargs.get("turn_id"))
        task_id = _clean(kwargs.get("task_id"))
        if session_id:
            key = f"session:{session_id}"
        elif session_key and session_key in self._approval_to_episode:
            return self._approval_to_episode[session_key]
        elif session_key:
            key = f"approval:{session_key}"
        elif turn_id and turn_id in self._turn_to_episode:
            return self._turn_to_episode[turn_id]
        elif task_id:
            key = f"task:{task_id}"
        else:
            # Never reuse an anonymous one-shot identity across callback calls.
            key = f"oneshot:{new_id('episode')}"

        existing = self.store.get_episode_by_key(key)
        if existing is not None:
            return existing.id
        with self._registry_lock:
            existing = self.store.get_episode_by_key(key)
            if existing is not None:
                return existing.id
            now = utc_now()
            episode_id = new_id("episode")
            episode = Episode(
                id=episode_id,
                key=key,
                session_id=session_id,
                task_id=task_id,
                platform=_clean(kwargs.get("platform")),
                model=_clean(kwargs.get("model")),
                default_stakes=self.config.default_stakes,
                current_turn=0,
                created_at=now,
                updated_at=now,
                compatibility_mode=self.compatibility.mode,
            )
            try:
                self.store.create_episode(episode, _correlation(kwargs))
            except sqlite3.IntegrityError:
                concurrent = self.store.get_episode_by_key(key)
                if concurrent is None:
                    raise
                return concurrent.id
            self._episode_locks[episode_id] = threading.RLock()
            return episode_id

    def bind_approval_session_key(self, session_key: str, episode_id: str) -> None:
        if session_key:
            with self._registry_lock:
                self._approval_to_episode[session_key] = episode_id

    @contextmanager
    def episode_lock(self, episode_id: str) -> Iterator[None]:
        with self._registry_lock:
            lock = self._episode_locks.setdefault(episode_id, threading.RLock())
        with lock:
            yield

    def query_for(self, episode_id: str) -> str:
        return self._queries.get(episode_id, "")

    def set_recent_tool_result(self, episode_id: str, result: str) -> None:
        self._recent_tool_results[episode_id] = result[:2_000]

    def recent_tool_result(self, episode_id: str) -> str:
        return self._recent_tool_results.get(episode_id, "")

    def mark_injection_failure(self, episode_id: str, reason: str) -> None:
        self._injection_failures.add(episode_id)
        self.health = Health.DEGRADED
        self.health_reasons.append(f"context injection failed for {episode_id}: {reason}")

    def injection_failed(self, episode_id: str) -> bool:
        return episode_id in self._injection_failures

    def finalize(self, episode_id: str, *, state: str = "finalized", **kwargs: Any) -> None:
        self.ensure_initialized()
        assert self.store is not None
        episode = self.store.get_episode(episode_id)
        if episode is None:
            raise EpisodeResolutionError("cannot finalize an unknown ledger episode")
        archived_key = f"closed:{episode.id}:{episode.key}"
        self.store.append_events(
            episode_id,
            [
                EventDraft(
                    "EPISODE_FINALIZED" if state == "finalized" else "EPISODE_RESET",
                    "episode",
                    episode_id,
                    {
                        "state": state,
                        "updated_at": utc_now(),
                        "episode_key": archived_key,
                    },
                )
            ],
            correlation=_correlation(kwargs),
        )
        with self._registry_lock:
            self._turn_configs.pop(episode_id, None)
            self._queries.pop(episode_id, None)
            self._recent_tool_results.pop(episode_id, None)
            self._injection_failures.discard(episode_id)
            for turn_id, mapped in list(self._turn_to_episode.items()):
                if mapped == episode_id:
                    self._turn_to_episode.pop(turn_id, None)
            for session_key, mapped in list(self._approval_to_episode.items()):
                if mapped == episode_id:
                    self._approval_to_episode.pop(session_key, None)

    def _reload_at_boundary(self) -> None:
        if self._config is None or not config_needs_reload(self._config):
            return
        try:
            snapshot, paths = load_config(hermes_home=self.hermes_home)
        except ConfigError as exc:
            self.health = Health.DEGRADED
            self.health_reasons.append(f"configuration reload rejected: {exc}")
            return
        if self.paths is not None and paths.database != self.paths.database:
            self.health = Health.DEGRADED
            self.health_reasons.append("database path changed; restart is required")
            return
        self._config = snapshot


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
        self.snapshot = config
        self.config = config.data
        self.scheduler = VerificationScheduler(store, self.config)
        self.gate = ActionGate(
            store,
            self.config,
            ActionPolicyRegistry(_action_policy_data(self.config)),
        )
        self.llm = HostLlmClient(lambda: runtime.ctx.llm, store, self.config)

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
        )
        evidence = Evidence(
            id=new_id("evidence"),
            episode_id=self.episode_id,
            kind="user_message",
            source_id=source.id,
            payload=prepared.payload,
            content_hash=prepared.full_hash,
            metadata={
                "sender_id_hash": content_hash(_clean(kwargs.get("sender_id"))),
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
                candidate, evidence, source, about_self=is_about_user_self(candidate.content)
            )
            drafts.extend(candidate_drafts)
        idempotency_key = "user:" + content_hash(
            canonical_json(
                [
                    self.episode_id,
                    _clean(kwargs.get("turn_id")),
                    prepared.full_hash,
                    "pre_llm_call",
                ]
            )
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
        adapted = self.runtime.adapters.adapt(
            tool_name,
            args,
            result,
            status=_clean(kwargs.get("status")),
            tool_call_id=_clean(kwargs.get("tool_call_id")),
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
        )
        evidence_id = new_id("evidence")
        wrapper_id = new_id("belief")
        metadata = {
            **adapted.metadata,
            "args_hash": _args_hash(args),
            "duration_ms": int(kwargs.get("duration_ms") or 0),
            "status": _clean(kwargs.get("status")),
            "error_type": _clean(kwargs.get("error_type")),
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
        initial = Belief(
            id=wrapper_id,
            episode_id=self.episode_id,
            content=adapted.wrapper_content,
            normalized_content=normalize_content(adapted.wrapper_content),
            pramana=Pramana.PRATYAKSHA,
            source_id=wrapper_source.id,
            evidence=(EvidenceRef(evidence_id),),
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
        trust = determine_admission(
            initial,
            wrapper_source,
            self.config,
            episode_stakes=self.episode.default_stakes,
        )
        wrapper = replace(initial, status=trust.status, admission_status=trust.status)
        validity = validate_belief(
            wrapper,
            evidence_payloads={evidence_id: prepared.payload},
            evidence_mode=str(storage["evidence_mode"]),
            max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
        )
        if not validity.valid:
            # A wrapper about observing a failed/unparsed result remains valid as
            # direct evidence only when the measured-only boundary holds. Parser
            # failure itself is what was observed.
            non_parser_reasons = [reason for reason in validity.reasons if "parsed" not in reason]
            if non_parser_reasons:
                wrapper = replace(wrapper, status=Status.OUT, admission_status=Status.OUT)
        support = IngestionSupport(
            id=new_id("support"),
            episode_id=self.episode_id,
            belief_id=wrapper.id,
            evidence_id=evidence.id,
            validity={**wrapper.validity, "checks": validity.checks},
        )
        drafts: list[EventDraft] = [
            _record_draft("EVIDENCE_INGESTED", "evidence", evidence.id, evidence),
            _record_draft("BELIEF_ADMITTED", "belief", wrapper.id, wrapper),
            _record_draft("INGESTION_SUPPORT_ADDED", "ingestion_support", support.id, support),
        ]
        drafts.extend(self._verification_drafts(wrapper, trust))
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
        if prepared.redacted:
            drafts.append(
                EventDraft(
                    "EVIDENCE_REDACTED",
                    "evidence",
                    evidence.id,
                    {"reason": "secret-like material removed before persistence"},
                )
            )
        if content_source and prepared.payload and adapted.content_assertive:
            drafts.append(
                EventDraft(
                    "UNPROMOTED_EVIDENCE_ADDED",
                    "evidence",
                    evidence.id,
                    {
                        "evidence_id": evidence.id,
                        "source_profile": adapted.adapter,
                        "reason": "lazy content extraction",
                    },
                )
            )
        idempotency_key = "tool:" + content_hash(
            canonical_json(
                [
                    self.episode_id,
                    _clean(kwargs.get("tool_call_id")),
                    "transform_tool_result",
                    prepared.full_hash,
                ]
            )
        )
        events = self.store.append_events(
            self.episode_id,
            drafts,
            correlation=_correlation(kwargs),
            idempotency_key=idempotency_key,
        )
        self.runtime.set_recent_tool_result(self.episode_id, result)
        self._after_new_beliefs()
        return tuple(event.id for event in events)

    def ensure_source(self, descriptor: SourceDescriptor | None) -> Source:
        if descriptor is None:
            raise ValueError("source descriptor is required")
        descriptor = _apply_source_profile(descriptor, self.config)
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
        scored: list[tuple[int, str]] = []
        query_tokens = set(normalize_content(query).split())
        for item in items:
            evidence = self.store.get_evidence(item["evidence_id"])
            score = (
                len(query_tokens & set(normalize_content(evidence.payload or "").split()))
                if evidence
                else 0
            )
            scored.append((score, item["evidence_id"]))
        scored.sort(key=lambda item: (-item[0], item[1]))
        all_event_ids: list[str] = []
        for _, evidence_id in scored[: int(self.config["ingestion"]["max_unpromoted_per_request"])]:
            all_event_ids.extend(self._promote_evidence(evidence_id))
        return tuple(all_event_ids)

    def _promote_evidence(self, evidence_id: str) -> tuple[str, ...]:
        evidence = self.store.get_evidence(evidence_id)
        if evidence is None or not evidence.payload:
            return ()
        content_source_id = str(evidence.metadata.get("content_source_id", ""))
        content_source = self.store.get_source(content_source_id)
        if content_source is None:
            return ()
        candidates: tuple[ClaimCandidate, ...]
        used_model = False
        direct_observation = evidence.metadata.get("content_pramana") == "pratyaksha"
        if direct_observation:
            candidates = deterministic_candidates(
                evidence.payload,
                pramana=Pramana.PRATYAKSHA,
                max_claims=int(self.config["ingestion"]["max_claims_per_evidence"]),
            )
        else:
            try:
                result = self.llm.complete_structured(
                    episode_id=self.episode_id,
                    purpose="belief-ledger.claim-extraction",
                    instructions=CLAIM_EXTRACTION,
                    text=evidence.payload,
                    schema=CLAIM_EXTRACTION_SCHEMA,
                    schema_name="belief_ledger_claim_extraction_v1",
                    max_tokens=1_500,
                    validator=_validate_claim_result,
                )
                candidates = result.parsed
                used_model = True
            except LlmComponentError:
                candidates = deterministic_candidates(
                    evidence.payload,
                    max_claims=int(self.config["ingestion"]["max_claims_per_evidence"]),
                )
        drafts: list[EventDraft] = []
        accepted = 0
        for candidate in candidates:
            validation = validate_candidate(
                candidate,
                evidence.payload,
                max_words=int(self.config["ingestion"]["max_atomic_claim_words"]),
                allowed_source_identity=content_source.name,
                allowed_pramanas={Pramana.PRATYAKSHA}
                if direct_observation
                else {Pramana.SHABDA, Pramana.ANUPALABDHI},
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
                accepted += 1
            drafts.extend(candidate_drafts)
        if accepted:
            drafts.append(
                EventDraft(
                    "UNPROMOTED_EVIDENCE_RESOLVED",
                    "evidence",
                    evidence.id,
                    {
                        "evidence_id": evidence.id,
                        "reason": f"promoted {accepted} validated claim(s)",
                    },
                )
            )
        else:
            drafts.append(
                EventDraft(
                    "CLAIM_EXTRACTION_FAILED",
                    "evidence",
                    evidence.id,
                    {
                        "reason": "no valid recoverable atomic claims",
                        "model_assisted": used_model,
                    },
                )
            )
        verdict_drafts = self._component_verdict_drafts(
            "claim_extractor",
            evidence.content_hash,
            "accepted" if accepted else "inconclusive",
            {"accepted": accepted, "model_assisted": used_model},
            premise_ids=(str(evidence.metadata.get("wrapper_belief_id", "")),),
        )
        drafts.extend(verdict_drafts)
        if not drafts:
            return ()
        events = self.store.append_events(self.episode_id, drafts)
        self._after_new_beliefs()
        return tuple(event.id for event in events)

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
        if not warrant.strip():
            raise ValueError("warrant must be non-empty")
        if not premise_ids:
            raise ValueError("at least one premise is required")
        premise_beliefs: list[Belief] = []
        for premise_id in premise_ids:
            premise = self.store.get_belief(premise_id)
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
        )
        if not validation.valid:
            raise ValueError("invalid inference: " + "; ".join(validation.reasons))
        trust = determine_admission(
            initial,
            source,
            self.config,
            episode_stakes=self.episode.default_stakes,
        )
        belief = replace(initial, status=trust.status, admission_status=trust.status)
        drafts = [_record_draft("BELIEF_ADMITTED", "belief", belief.id, belief)]
        drafts.extend(self._verification_drafts(belief, trust))
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
        belief = self.store.get_belief(task.belief_id)
        if belief is None:
            raise ValueError("verification belief is missing")
        drafts: list[EventDraft] = [
            EventDraft(
                "VERIFICATION_TASK_COMPLETED",
                "verification_task",
                task.id,
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
                            "cause": f"verification:{task.id}:{result}",
                        },
                    )
                )
            source = self.store.get_source(belief.source_id)
            if source:
                stats = SourceStats(
                    confirmed=source.stats.confirmed + int(result == "confirmed"),
                    defeated=source.stats.defeated + int(result == "disconfirmed"),
                    samples=source.stats.samples + 1,
                )
                drafts.append(
                    EventDraft(
                        "SOURCE_STATS_UPDATED",
                        "source",
                        source.id,
                        {"stats": to_primitive(stats), "competence": dict(source.competence)},
                    )
                )
        events = self.store.append_events(self.episode_id, drafts)
        transition_ids = self.relabel()
        return tuple(event.id for event in events) + transition_ids

    def run_chain_audit(self, task: VerificationTask) -> tuple[str, ...]:
        belief = self.store.get_belief(task.belief_id)
        if belief is None or not belief.justifications:
            return self.complete_verification(
                task, "disconfirmed", cause="derived belief has no justification"
            )
        all_events: list[str] = []
        for justification in belief.justifications:
            statuses = {
                premise_id: premise.status
                for premise_id in justification.premises
                if (premise := self.store.get_belief(premise_id)) is not None
            }
            missing = local_asiddha(justification, statuses)
            if missing:
                return self.complete_verification(
                    task,
                    "disconfirmed",
                    cause="asiddha premises: " + ",".join(missing),
                )
            payload = canonical_json(
                {
                    "belief": {"id": belief.id, "content": belief.content},
                    "premises": [
                        {
                            "id": premise_id,
                            "content": (self.store.get_belief(premise_id) or belief).content,
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
            verdict_drafts = self._component_verdict_drafts(
                "chain_auditor",
                content_hash(payload),
                "passed" if not audit.fallacies else "failed",
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
            if audit.fallacies:
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
                        basis="chain audit: " + ",".join(audit.fallacies),
                    )
                    drafts.append(_record_draft("DEFEAT_ADDED", "defeat", edge.id, edge))
            events = self.store.append_events(self.episode_id, drafts)
            all_events.extend(event.id for event in events)
            if audit.fallacies or not (
                audit.paksadharmata and audit.sapakse_sattvam and audit.vipakse_asattvam
            ):
                all_events.extend(
                    self.complete_verification(task, "disconfirmed", cause="chain audit failed")
                )
                return tuple(all_events)
        all_events.extend(
            self.complete_verification(task, "confirmed", cause="trairupya chain audit passed")
        )
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
            )
            drafts: list[EventDraft] = []
            for edge in defeats:
                active = outcome.active_edges.get(edge.id, False)
                if active != edge.active:
                    drafts.append(
                        EventDraft(
                            "DEFEAT_ACTIVITY_CHANGED",
                            "defeat",
                            edge.id,
                            {"active": active},
                        )
                    )

            active_notices = {
                notice.defeated_belief_id
                for notice in self.store.list_retractions(self.episode_id, state="active")
            }
            current_turn = self.episode.current_turn
            ttl = int(self.config["context"]["retraction_ttl_turns"])
            defeated_by_source: dict[str, int] = {}
            for belief_id in sorted(outcome.statuses):
                old = beliefs[belief_id]
                new_status = outcome.statuses[belief_id]
                if old.status is new_status:
                    continue
                cause = outcome.causes.get(belief_id, "fixed_point_relabel")
                drafts.append(
                    EventDraft(
                        "BELIEF_STATUS_CHANGED",
                        "belief",
                        belief_id,
                        {"from": old.status.value, "to": new_status.value, "cause": cause},
                    )
                )
                if old.status is Status.IN and new_status is Status.OUT:
                    defeated_by_source[old.source_id] = defeated_by_source.get(old.source_id, 0) + 1
                    if (
                        self.store.was_rendered(self.episode_id, belief_id)
                        and belief_id not in active_notices
                    ):
                        notice = RetractionNotice(
                            id=new_id("retraction"),
                            episode_id=self.episode_id,
                            defeated_belief_id=belief_id,
                            cause=cause,
                            descendants=self.store.descendants(self.episode_id, belief_id),
                            created_turn=current_turn,
                            ttl_turns=ttl,
                        )
                        drafts.append(
                            _record_draft("RETRACTION_CREATED", "retraction", notice.id, notice)
                        )

            existing_conflicts: dict[tuple[str, str], Conflict] = {}
            for conflict in self.store.list_conflicts(self.episode_id, state="open"):
                conflict_pair = (
                    (conflict.left_belief_id, conflict.right_belief_id)
                    if conflict.left_belief_id <= conflict.right_belief_id
                    else (conflict.right_belief_id, conflict.left_belief_id)
                )
                existing_conflicts[conflict_pair] = conflict
            new_conflicts = set(outcome.conflicts)
            for pair in sorted(new_conflicts - set(existing_conflicts)):
                task = VerificationTask(
                    id=new_id("verification"),
                    episode_id=self.episode_id,
                    belief_id=pair[0],
                    method=VerificationMethod.CROSS_SOURCE,
                    k_required=1,
                    budget=1,
                )
                conflict = Conflict(
                    id=new_id("conflict"),
                    episode_id=self.episode_id,
                    left_belief_id=pair[0],
                    right_belief_id=pair[1],
                    normalized_scope={},
                    verification_task_id=task.id,
                )
                drafts.append(
                    _record_draft("VERIFICATION_TASK_CREATED", "verification_task", task.id, task)
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

            for source_id, count in sorted(defeated_by_source.items()):
                source = sources[source_id]
                stats = SourceStats(
                    confirmed=source.stats.confirmed,
                    defeated=source.stats.defeated + count,
                    samples=source.stats.samples + count,
                )
                drafts.append(
                    EventDraft(
                        "SOURCE_STATS_UPDATED",
                        "source",
                        source_id,
                        {"stats": to_primitive(stats), "competence": dict(source.competence)},
                    )
                )
            if outcome.oscillation:
                drafts.append(
                    EventDraft(
                        "DEFEAT_CYCLE_SAMSAYA",
                        "episode",
                        self.episode_id,
                        {"iterations": outcome.iterations},
                    )
                )
            if not drafts:
                return ()
            events = self.store.append_events(self.episode_id, drafts)
            return tuple(event.id for event in events)

    def compile_context(
        self,
        *,
        query: str = "",
        request_id: str = "",
        pending_tool_intent: str = "",
        ascii_only: bool = False,
    ) -> RenderedContext:
        effective_query = "\n".join(
            item
            for item in (
                query or self.runtime.query_for(self.episode_id),
                pending_tool_intent,
                self.runtime.recent_tool_result(self.episode_id),
            )
            if item
        )
        self.promote_relevant(effective_query)
        self._run_one_relevant_chain_audit(effective_query)
        self.relabel()
        beliefs = self.store.list_beliefs(self.episode_id)
        sources = {source.id: source for source in self.store.list_sources(self.episode_id)}
        selection = select_beliefs(
            beliefs,
            sources,
            query=effective_query,
            conflicts=self.store.list_conflicts(self.episode_id),
            retractions=self.store.list_retractions(self.episode_id),
            retrieval_ids=self.store.fts_belief_ids(
                self.episode_id,
                effective_query,
                limit=int(self.config["context"]["max_beliefs"]) * 4,
            )
            if self.config["context"].get("relevance") == "fts5"
            else (),
            config=self.config,
        )
        resolved_request_id = request_id or new_id("event")
        rendered = render_context(
            selection,
            sources,
            config=self.config,
            health=self.runtime.health,
            request_id=resolved_request_id,
            ascii_only=ascii_only,
        )
        now = utc_now()
        event = self.store.append_events(
            self.episode_id,
            [
                EventDraft(
                    "CONTEXT_COMPILED",
                    "episode",
                    self.episode_id,
                    {
                        "request_id": resolved_request_id,
                        "config_digest": self.snapshot.digest,
                        "query_hash": content_hash(effective_query),
                        "truncated": rendered.truncated,
                        "rendered": [
                            {
                                "belief_id": belief_id,
                                "request_id": resolved_request_id,
                                "turn_number": self.episode.current_turn,
                                "rendered_at": now,
                            }
                            for belief_id in rendered.belief_ids
                        ],
                    },
                )
            ],
        )
        del event
        return rendered

    def lint_and_enforce(self, response: str, **kwargs: Any) -> str | None:
        stakes = self.episode.default_stakes
        marker = str(self.config["lint"]["pending_marker"])
        beliefs = self.store.list_beliefs(self.episode_id)
        input_observation = self._observe_component_input("output_linter", response)

        def relint(text: str) -> LintReport:
            return lint_response(
                text, self.store.list_beliefs(self.episode_id), pending_marker=marker
            )

        report = lint_response(response, beliefs, pending_marker=marker)
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
            )
        except LlmComponentError:
            replacement = linter_failure_response(stakes, response)
            enforced = LintReport(report.claims, False, replacement, ("rewrite component failed",))
        final_text = enforced.replacement if enforced.replacement is not None else response
        verdict_drafts = self._component_verdict_drafts(
            "output_linter",
            content_hash(response),
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
                content_hash(response),
                {
                    "response_hash": content_hash(response),
                    "passed": enforced.passed,
                    "report": to_primitive(enforced),
                },
            ),
            *verdict_drafts,
        ]
        used_beliefs = {
            belief_id for claim in enforced.claims for belief_id in claim.supporting_beliefs
        }
        for notice in self.store.list_retractions(self.episode_id):
            retracted = {notice.defeated_belief_id, *notice.descendants}
            if not (used_beliefs & retracted):
                drafts.append(
                    EventDraft(
                        "RETRACTION_ACKNOWLEDGED",
                        "retraction",
                        notice.id,
                        {"response_hash": content_hash(final_text)},
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
            claim_tokens = set(normalize_content(claim.text).split()) - stop
            ranked = sorted(
                (
                    (
                        len(claim_tokens & (set(belief.normalized_content.split()) - stop)),
                        belief,
                    )
                    for belief in beliefs
                    if belief.status in {Status.IN, Status.PENDING}
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
                content_hash(payload),
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

        input_hash = content_hash(text)
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
        storage = self.config["storage"]
        prepared = prepare_evidence(
            text,
            mode=str(storage["evidence_mode"]),
            max_excerpt_chars=int(storage["max_excerpt_chars"]),
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
        trust = determine_admission(
            initial,
            source,
            self.config,
            episode_stakes=self.episode.default_stakes,
        )
        belief = replace(initial, status=trust.status, admission_status=trust.status)
        support = IngestionSupport(
            id=new_id("support"),
            episode_id=self.episode_id,
            belief_id=belief.id,
            evidence_id=evidence.id,
            validity=dict(belief.validity),
        )
        events = self.store.append_events(
            self.episode_id,
            [
                _record_draft("EVIDENCE_INGESTED", "evidence", evidence.id, evidence),
                _record_draft("BELIEF_ADMITTED", "belief", belief.id, belief),
                _record_draft("INGESTION_SUPPORT_ADDED", "ingestion_support", support.id, support),
                *self._verification_drafts(belief, trust),
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
            response, self.store.list_beliefs(self.episode_id), pending_marker=marker
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
        redacted, _ = redact_secrets(response)
        event = self.store.append_events(
            self.episode_id,
            [
                EventDraft(
                    "ASSISTANT_RESPONSE_RECORDED",
                    "response",
                    content_hash(response),
                    {
                        "turn_id": _clean(kwargs.get("turn_id")),
                        "content_hash": content_hash(response),
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
        return self.gate.evaluate(self.episode_id, tool_name, args, description=description)

    def query(
        self,
        text: str,
        *,
        statuses: Sequence[Status] = (),
        pramanas: Sequence[Pramana] = (),
        limit: int = 20,
        expand_graph: bool = False,
    ) -> list[dict[str, Any]]:
        wanted = set(normalize_content(text).split())
        beliefs = self.store.list_beliefs(
            self.episode_id,
            statuses=statuses or None,
            pramanas=pramanas or None,
            limit=5_000,
        )
        scored = []
        for belief in beliefs:
            score = len(wanted & set(belief.normalized_content.split()))
            if not wanted or score:
                scored.append((score, belief))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            {
                "id": belief.id,
                "content": belief.content,
                "pramana": belief.pramana.value,
                "status": belief.status.value,
                "source_id": belief.source_id,
                "qualifiers": belief.qualifiers,
                "premises": [
                    premise
                    for justification in belief.justifications
                    for premise in justification.premises
                ]
                if expand_graph
                else [],
            }
            for _, belief in scored[: max(1, min(limit, 100))]
        ]

    def explain(self, belief_id: str, *, depth: int = 4) -> dict[str, Any]:
        belief = self.store.get_belief(belief_id)
        if belief is None or belief.episode_id != self.episode_id:
            raise ValueError("belief does not exist in this episode")
        source = self.store.get_source(belief.source_id)
        if source is None:
            raise RuntimeUnavailable("belief source projection is missing")
        all_sources = {item.id: item for item in self.store.list_sources(self.episode_id)}
        trace = priority_trace(belief, source, self.config)
        defeats = [
            edge
            for edge in self.store.list_defeats(self.episode_id)
            if edge.attacker == belief_id or edge.target == belief_id
        ]
        events = [
            event
            for event in self.store.events(self.episode_id)
            if event.aggregate_id == belief_id or event.payload.get("belief_id") == belief_id
        ]
        tasks = [
            task
            for task in self.store.list_verification_tasks(self.episode_id, state=None)
            if task.belief_id == belief_id
        ]
        return {
            "belief": to_primitive(belief),
            "source": to_primitive(source),
            "priority": to_primitive(trace),
            "defeats": [to_primitive(edge) for edge in defeats],
            "transitions": [
                to_primitive(event) for event in events[-max(1, min(depth * 5, 100)) :]
            ],
            "verification": [to_primitive(task) for task in tasks],
            "live_justifications": [
                justification.id
                for justification in belief.justifications
                if all(
                    (premise := self.store.get_belief(premise_id)) is not None
                    and premise.status is Status.IN
                    for premise_id in justification.premises
                )
            ],
            "source_count": len(all_sources),
        }

    def set_stakes(self, stakes: Stakes, *, user_initiated: bool) -> tuple[str, ...]:
        current = self.episode.default_stakes
        ranks = {Stakes.LOW: 0, Stakes.MED: 1, Stakes.HIGH: 2, Stakes.CRITICAL: 3}
        if ranks[stakes] < ranks[current] and not user_initiated:
            raise ValueError("only an explicit user command may lower stakes")
        if stakes is current:
            return ()
        events = self.store.append_events(
            self.episode_id,
            [
                EventDraft(
                    "EPISODE_STAKES_CHANGED",
                    "episode",
                    self.episode_id,
                    {"from": current.value, "to": stakes.value, "user_initiated": user_initiated},
                )
            ],
        )
        return tuple(event.id for event in events)

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
        for existing in self.store.find_exact_beliefs(self.episode_id, normalized):
            existing_source = self.store.get_source(existing.source_id)
            if existing_source and existing_source.root == source.root:
                return [
                    EventDraft(
                        "DUPLICATE_CONTENT_OBSERVED",
                        "belief",
                        existing.id,
                        {"evidence_id": evidence.id, "source_root": source.root},
                    )
                ]
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
        trust = determine_admission(
            initial,
            source,
            self.config,
            episode_stakes=self.episode.default_stakes,
        )
        belief = replace(initial, status=trust.status, admission_status=trust.status)
        support = IngestionSupport(
            id=new_id("support"),
            episode_id=self.episode_id,
            belief_id=belief.id,
            evidence_id=evidence.id,
            validity={**validity, "checks": validation.checks},
        )
        drafts = [
            _record_draft("BELIEF_ADMITTED", "belief", belief.id, belief),
            _record_draft("INGESTION_SUPPORT_ADDED", "ingestion_support", support.id, support),
        ]
        drafts.extend(self._verification_drafts(belief, trust))
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
        assessment = assess_negative_search(
            search_succeeded=search_succeeded,
            truncated=bool(args.get("truncated", True)),
            corpus=str(args.get("corpus") or ""),
            scope=str(args.get("scope") or ""),
            query=str(args.get("query") or args.get("pattern") or ""),
            parameters=parameters,
            coverage=float(args.get("coverage") or 0.0),
            recall=float(args.get("recall") or 0.0),
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
        trust = determine_admission(
            initial,
            source,
            self.config,
            episode_stakes=self.episode.default_stakes,
        )
        belief = replace(initial, status=trust.status, admission_status=trust.status)
        support = IngestionSupport(
            id=new_id("support"),
            episode_id=self.episode_id,
            belief_id=belief.id,
            evidence_id=evidence.id,
            validity={**assessment.validity, "checks": validation.checks},
        )
        drafts = [
            _record_draft("BELIEF_ADMITTED", "belief", belief.id, belief),
            _record_draft("INGESTION_SUPPORT_ADDED", "ingestion_support", support.id, support),
        ]
        drafts.extend(self._verification_drafts(belief, trust))
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
        valid_premises = tuple(
            premise_id
            for premise_id in dict.fromkeys(premise_ids)
            if premise_id
            and (premise := self.store.get_belief(premise_id)) is not None
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
            trust = determine_admission(
                initial,
                source,
                self.config,
                episode_stakes=self.episode.default_stakes,
            )
            belief = replace(initial, status=trust.status, admission_status=trust.status)
            verdict = replace(verdict, belief_id=belief.id)
            drafts.append(_record_draft("BELIEF_ADMITTED", "belief", belief.id, belief))
            drafts.extend(self._verification_drafts(belief, trust))
        drafts.append(
            _record_draft("COMPONENT_VERDICT_RECORDED", "component_verdict", verdict.id, verdict)
        )
        return drafts

    def _after_new_beliefs(self) -> None:
        self._detect_deterministic_rebuts()
        self.relabel()
        if self._complete_passive_tasks():
            self.relabel()

    def _complete_passive_tasks(self) -> tuple[str, ...]:
        tasks = self.store.list_verification_tasks(self.episode_id, state="open")
        if not tasks:
            return ()
        beliefs = self.store.list_beliefs(self.episode_id)
        sources = {source.id: source for source in self.store.list_sources(self.episode_id)}
        event_ids: list[str] = []
        for task in tasks:
            belief = self.store.get_belief(task.belief_id)
            if belief is None:
                continue
            if task.method is VerificationMethod.CROSS_SOURCE:
                count = self.scheduler.passive_cross_source_count(belief, beliefs, sources)
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
                    for candidate in beliefs
                    if candidate.id != belief.id
                    and candidate.pramana is Pramana.PRATYAKSHA
                    and candidate.status is Status.IN
                    and candidate.normalized_content == belief.normalized_content
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
        for task in self.store.list_verification_tasks(self.episode_id, state="open"):
            if task.method is not VerificationMethod.CHAIN_AUDIT:
                continue
            belief = self.store.get_belief(task.belief_id)
            if belief is None:
                continue
            score = len(query_tokens & set(belief.normalized_content.split()))
            candidates.append((score, task))
        if not candidates:
            return ()
        candidates.sort(key=lambda item: (-item[0], item[1].id))
        return self.run_chain_audit(candidates[0][1])

    def _detect_deterministic_rebuts(self) -> tuple[str, ...]:
        beliefs = self.store.list_beliefs(self.episode_id)
        defeats = self.store.list_defeats(self.episode_id)
        existing = {
            (edge.attacker, edge.target) for edge in defeats if edge.kind is DefeatKind.REBUT
        }
        token_index: dict[str, list[Belief]] = {}
        drafts: list[EventDraft] = []
        considered: set[tuple[str, str]] = set()
        semantic_candidate: tuple[Belief, Belief] | None = None
        for belief in sorted(beliefs, key=lambda item: item.id):
            tokens = set(belief.normalized_content.split())
            candidates: dict[str, Belief] = {}
            for token in tokens:
                for candidate in token_index.get(token, ()):
                    candidates[candidate.id] = candidate
            for other in sorted(candidates.values(), key=lambda item: item.id):
                pair = (belief.id, other.id) if belief.id <= other.id else (other.id, belief.id)
                if pair in considered:
                    continue
                considered.add(pair)
                if not candidate_pair(belief, other):
                    continue
                decision = classify_deterministically(belief, other)
                if (
                    decision.outcome == "uncertain"
                    and semantic_candidate is None
                    and belief.domain not in {"runtime_state", "monitoring"}
                    and other.domain not in {"runtime_state", "monitoring"}
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
        if semantic_candidate is not None:
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
        payload = canonical_json(
            {
                "left": {
                    "id": left.id,
                    "content": left.content,
                    "qualifiers": left.qualifiers,
                },
                "right": {
                    "id": right.id,
                    "content": right.content,
                    "qualifiers": right.qualifiers,
                },
            }
        )
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
            return []
        outcome = result.parsed
        drafts = self._component_verdict_drafts(
            "contradiction_classifier",
            content_hash(payload),
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


def _record_draft(kind: str, aggregate_type: str, aggregate_id: str, record: Any) -> EventDraft:
    return EventDraft(kind, aggregate_type, aggregate_id, {"record": to_primitive(record)})


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _correlation(kwargs: Mapping[str, Any]) -> dict[str, str]:
    fields = (
        "session_id",
        "session_key",
        "task_id",
        "turn_id",
        "tool_call_id",
        "api_request_id",
        "parent_session_id",
        "child_session_id",
    )
    return {field: value for field in fields if (value := _clean(kwargs.get(field)))}


def _args_hash(args: dict[str, Any]) -> str:
    serialized = json.dumps(
        args, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    redacted, _ = redact_secrets(serialized)
    return content_hash(redacted)


def _validate_claim_result(value: Any) -> tuple[ClaimCandidate, ...]:
    if not isinstance(value, dict) or not isinstance(value.get("claims"), list):
        raise ValueError("claim extractor result must contain a claims array")
    if len(value["claims"]) > 24:
        raise ValueError("claim extractor returned too many claims")
    return tuple(candidate_from_structured(item) for item in value["claims"])


def _validate_rewrite(value: Any) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("response"), str):
        raise ValueError("rewrite result must contain a response string")
    if len(value["response"]) > 16_000:
        raise ValueError("rewrite response exceeds limit")
    return str(value["response"])


def _validate_contradiction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("contradiction result must be an object")
    outcome = value.get("outcome")
    if outcome not in {"rebut", "compatible", "scope_mismatch", "uncertain"}:
        raise ValueError("contradiction outcome is invalid")
    if not isinstance(value.get("basis"), str) or not value["basis"].strip():
        raise ValueError("contradiction basis is required")
    for key in ("left_scope", "right_scope"):
        scope = value.get(key)
        if not isinstance(scope, dict) or not all(
            isinstance(item_key, str) and isinstance(item, str) for item_key, item in scope.items()
        ):
            raise ValueError(f"{key} is invalid")
    return {
        "outcome": str(outcome),
        "basis": value["basis"].strip(),
        "left_scope": value["left_scope"],
        "right_scope": value["right_scope"],
    }


def _validate_entailment(value: Any, allowed: set[tuple[int, str]]) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, dict) or not isinstance(value.get("pairs"), list):
        raise ValueError("entailment result must contain pairs")
    if len(value["pairs"]) > 30:
        raise ValueError("entailment result exceeds pair limit")
    parsed: list[dict[str, Any]] = []
    for item in value["pairs"]:
        if not isinstance(item, dict):
            raise ValueError("entailment pair must be an object")
        index = item.get("claim_index")
        belief_id = item.get("belief_id")
        entailed = item.get("entailed")
        basis = item.get("basis")
        if (
            not isinstance(index, int)
            or not isinstance(belief_id, str)
            or (index, belief_id) not in allowed
            or not isinstance(entailed, bool)
            or not isinstance(basis, str)
        ):
            raise ValueError("entailment pair is invalid or outside candidates")
        parsed.append(
            {
                "claim_index": index,
                "belief_id": belief_id,
                "entailed": entailed,
                "basis": basis[:300],
            }
        )
    return tuple(parsed)


def _action_policy_data(config: dict[str, Any]) -> dict[str, Any]:
    packaged = packaged_yaml("action-policies.yaml")
    extension_rules: list[dict[str, Any]] = []
    for raw_path in config.get("gating", {}).get("policy_files", []):
        path = Path(str(raw_path)).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError(f"action policy extension is unavailable or too large: {path}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError(f"action policy extension schema is invalid: {path}")
        rules = value.get("rules")
        if not isinstance(rules, list) or not all(isinstance(item, dict) for item in rules):
            raise ValueError(f"action policy extension rules are invalid: {path}")
        extension_rules.extend(rules)
    return {"schema_version": 1, "rules": [*extension_rules, *packaged["rules"]]}


def _apply_source_profile(descriptor: SourceDescriptor, config: dict[str, Any]) -> SourceDescriptor:
    if descriptor.kind is SourceKind.RETRIEVER:
        return descriptor
    profiles = dict(packaged_yaml("source-profiles.yaml")["profiles"])
    for raw_path in config.get("trust", {}).get("source_profile_files", []):
        path = Path(str(raw_path)).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError(f"source profile extension is unavailable or too large: {path}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError(f"source profile extension schema is invalid: {path}")
        additions = value.get("profiles")
        if not isinstance(additions, dict):
            raise ValueError(f"source profile extension profiles are invalid: {path}")
        profiles.update(additions)
    profile_name = {
        SourceKind.TOOL: "hermes_tool",
        SourceKind.DOCUMENT: "workspace_file",
        SourceKind.WEB: "official_web" if descriptor.integrity is Integrity.SEMI else "open_web",
        SourceKind.USER: "user",
        SourceKind.MODEL: "model_component",
        SourceKind.LEDGER: "prior_ledger",
    }[descriptor.kind]
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        return descriptor
    try:
        return replace(
            descriptor,
            kind=SourceKind(str(profile.get("kind", descriptor.kind.value))),
            integrity=Integrity(str(profile.get("integrity", descriptor.integrity.value))),
            competence={
                str(key): float(value)
                for key, value in dict(profile.get("competence", descriptor.competence)).items()
            },
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"source profile {profile_name} is invalid") from exc
