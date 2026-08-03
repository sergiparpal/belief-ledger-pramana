"""Public host-neutral orchestration facade built from the existing core primitives."""

from __future__ import annotations

import copy
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, replace
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from .api_types import (
    ActionAuthorization,
    ActionPermit,
    BeliefLedgerError,
    ChainVerification,
    DecisionExplanation,
    EpisodeHandle,
    EvidenceAdmission,
    EvidenceObservation,
    OutputEvaluation,
    PermissionConsumption,
)
from .buffering import ResponseGate
from .config import CoreConfig, CoreConfigSnapshot, validate_core_config
from .contracts import (
    ApprovalResult,
    EnforcementProfile,
    EpisodeContext,
    HostCapabilities,
    NormalizedDecision,
    OutputCandidate,
    ToolInvocation,
    ToolResult,
    negotiate_profile,
)
from .dependencies import RuntimeDependencies, system_dependencies
from .enforcement import ActionBinding, ApprovalBinding, ApprovalReceipt, EnforcementStore
from .engine.trust import determine_admission
from .engine.validity import normalize_content, validate_belief
from .errors import StoreError
from .events import EventDraft, canonical_json, content_hash, to_primitive
from .gate.classify import ActionPolicyRegistry
from .gate.decision import ActionGate
from .gate.preconditions import resolve_preconditions
from .ingestion.tool import prepare_evidence
from .lint.enforce import enforce_report
from .lint.report import lint_response
from .manifest import InventoryItem, ToolDescriptor, ToolPolicy, ToolPolicyManifest
from .models import (
    Belief,
    CompatibilityMode,
    Episode,
    Evidence,
    EvidenceRef,
    GateDecision,
    GateOutcome,
    IngestionSupport,
    Integrity,
    Justification,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    Status,
    VerificationTask,
)
from .store import LedgerStore, ReplayResult


class BeliefLedger:
    """Generic decision service with explicit state ownership.

    The facade decides, records, issues, and consumes bound permissions. It never executes an
    arbitrary callback and never claims ownership of a host's output sink.
    """

    def __init__(
        self,
        *,
        state_root: Path,
        config: CoreConfigSnapshot,
        dependencies: RuntimeDependencies,
        capabilities: HostCapabilities,
        requested_profile: EnforcementProfile,
        manifest: ToolPolicyManifest,
    ) -> None:
        self.state_root = state_root
        self.config = config
        self.dependencies = dependencies
        self.capabilities = capabilities
        self.profile_selection = negotiate_profile(capabilities, requested_profile)
        if self.profile_selection.missing and requested_profile is not EnforcementProfile.OBSERVE:
            raise BeliefLedgerError(
                "CAPABILITY_SHORTFALL",
                f"{requested_profile.value}:{','.join(self.profile_selection.missing)}",
            )
        self.manifest = manifest
        storage = config.data["storage"]
        database = storage.get("database") or "ledger.sqlite3"
        database_path = Path(str(database))
        if not database_path.is_absolute():
            database_path = state_root / database_path
        if database_path.is_symlink():
            raise BeliefLedgerError(
                "DATABASE_SYMLINK", "storage.database must not be a symbolic link"
            )
        database_path = database_path.resolve(strict=False)
        if not database_path.is_relative_to(state_root):
            raise BeliefLedgerError(
                "DATABASE_OUTSIDE_STATE_ROOT",
                "storage.database must remain inside the explicit state root",
            )
        self.store = LedgerStore(
            database_path,
            busy_timeout_ms=int(storage["busy_timeout_ms"]),
            integrity_key_path=state_root / ".ledger.integrity.key",
        )
        self.enforcement = EnforcementStore(
            self.store.database,
            dependencies,
            busy_timeout_ms=int(storage["busy_timeout_ms"]),
        )

    @classmethod
    def open(
        cls,
        *,
        state_root: Path,
        config: CoreConfig | CoreConfigSnapshot | Mapping[str, Any] | None = None,
        dependencies: RuntimeDependencies | None = None,
        capabilities: HostCapabilities | None = None,
        requested_profile: EnforcementProfile = EnforcementProfile.OBSERVE,
        manifest: ToolPolicyManifest | Mapping[str, Any] | None = None,
    ) -> BeliefLedger:
        """Open an explicitly owned state root; no adapter or host path is inspected."""

        requested_root = state_root.expanduser().absolute()
        if requested_root.is_symlink():
            raise BeliefLedgerError("STATE_ROOT_SYMLINK", "state_root must not be a symbolic link")
        root = requested_root.resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with suppress(OSError):
            root.chmod(0o700)
        if os.name != "nt":
            metadata = root.stat()
            getuid = getattr(os, "getuid", None)
            if (
                not callable(getuid)
                or metadata.st_uid != getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise BeliefLedgerError(
                    "INSECURE_STATE_ROOT",
                    "state_root must be owned by the current user and private",
                )
        snapshot = _configuration(root, config)
        selected_manifest = _manifest(manifest, mode=str(snapshot.data["mode"]))
        return cls(
            state_root=root,
            config=snapshot,
            dependencies=dependencies or system_dependencies(),
            capabilities=capabilities or HostCapabilities(),
            requested_profile=requested_profile,
            manifest=selected_manifest,
        )

    @property
    def effective_profile(self) -> EnforcementProfile:
        return self.profile_selection.effective

    @property
    def policy_digest(self) -> str:
        """Digest the live policy value so authorization bindings cannot use stale content."""

        return content_hash(canonical_json(self.manifest.as_dict()))

    def start_episode(self, context: EpisodeContext) -> EpisodeHandle:
        """Start or reopen the episode identified by the normalized context."""

        if type(context.schema_version) is not int or context.schema_version != 1:
            raise BeliefLedgerError("UNSUPPORTED_SCHEMA", "episode context schema is unsupported")
        key = content_hash(
            canonical_json(
                {
                    "session_id": context.persisted_session_id,
                    "task_id": context.persisted_task_id,
                    "platform": context.platform,
                }
            )
        )
        existing = self.store.get_episode_by_key(key)
        if existing is not None and existing.state == "active":
            return EpisodeHandle(1, existing.id, existing.state)
        if existing is not None:
            self.store.append_events(
                existing.id,
                [
                    EventDraft(
                        "EPISODE_RESET",
                        "episode",
                        existing.id,
                        {
                            "state": existing.state,
                            "episode_key": f"closed:{existing.id}",
                            "updated_at": self.dependencies.clock.now(),
                        },
                    )
                ],
            )
        now = self.dependencies.clock.now()
        episode = Episode(
            self.dependencies.identity.new("episode"),
            key,
            context.persisted_session_id,
            context.persisted_task_id,
            context.platform,
            context.model,
            Stakes(str(self.config.data["default_stakes"])),
            1,
            now,
            now,
            CompatibilityMode.FULL,
        )
        self.store.create_episode(episode, correlation=dict(context.correlation))
        self.store.append_events(
            episode.id,
            [
                EventDraft(
                    "HOST_CAPABILITIES_RECORDED",
                    "episode",
                    episode.id,
                    {
                        "capabilities": to_primitive(self.capabilities),
                        "requested_profile": self.profile_selection.requested.value,
                        "effective_profile": self.profile_selection.effective.value,
                        "reason_codes": list(self.profile_selection.reason_codes),
                    },
                )
            ],
        )
        return EpisodeHandle(1, episode.id, episode.state)

    def finalize_episode(self, episode_id: str) -> EpisodeHandle:
        episode = self._episode(episode_id)
        if episode.state != "finalized":
            self.store.append_events(
                episode_id,
                [
                    EventDraft(
                        "EPISODE_FINALIZED",
                        "episode",
                        episode_id,
                        {
                            "state": "finalized",
                            "episode_key": f"closed:{episode_id}",
                            "updated_at": self.dependencies.clock.now(),
                        },
                    )
                ],
            )
        # Always revoke. revoke_for_episode only touches state='issued' rows, so it is
        # idempotent, and running it unconditionally lets a retry repair a finalize that
        # committed the lifecycle event and then failed before revocation.
        self.enforcement.revoke_for_episode(episode_id)
        return EpisodeHandle(1, episode_id, "finalized")

    def ingest_evidence(
        self, episode_id: str, observation: EvidenceObservation
    ) -> EvidenceAdmission:
        """Admit a normalized observation after privacy preparation."""

        episode = self._episode(episode_id, require_active=True)
        if type(observation.schema_version) is not int or observation.schema_version != 1:
            raise BeliefLedgerError("UNSUPPORTED_SCHEMA", "evidence schema is unsupported")
        try:
            kind = SourceKind(observation.source_kind)
            integrity = Integrity(observation.source_integrity)
            pramana = Pramana(observation.pramana)
            stakes = Stakes(observation.stakes)
            perishability = Perishability(observation.perishability)
        except ValueError as exc:
            raise BeliefLedgerError("INVALID_OBSERVATION", str(exc)) from exc
        retention = observation.retention_mode
        if retention not in {"hash_only", "excerpt", "full"}:
            raise BeliefLedgerError("INVALID_RETENTION_MODE", retention)
        source = self.store.find_source(episode_id, observation.provenance_root, kind)
        drafts: list[EventDraft] = []
        if source is None:
            source = Source(
                self.dependencies.identity.new("source"),
                episode_id,
                kind,
                integrity,
                observation.source_name,
                observation.provenance_root,
                {},
                SourceStats(),
            )
            drafts.append(_record("SOURCE_REGISTERED", "source", source.id, source))
        elif source.integrity is not integrity or source.name != observation.source_name:
            raise BeliefLedgerError(
                "SOURCE_METADATA_MISMATCH",
                "an existing provenance root cannot change its name or integrity",
            )
        prepared = prepare_evidence(
            observation.content,
            mode=retention,
            max_excerpt_chars=int(self.config.data["storage"]["max_excerpt_chars"]),
            redact=bool(self.config.data["storage"]["redact_secrets"]),
        )
        evidence = Evidence(
            self.dependencies.identity.new("evidence"),
            episode_id,
            observation.kind,
            source.id,
            prepared.payload,
            prepared.full_hash,
            {
                "subject": observation.subject,
                "target": observation.target,
                "correlation": dict(observation.correlation),
                "retention_mode": retention,
                "observed_chars": prepared.observed_chars,
            },
            observation.observed_at or self.dependencies.clock.now(),
            prepared.redacted,
        )
        if retention == "hash_only" and not observation.belief_content:
            raise BeliefLedgerError(
                "HASH_ONLY_REQUIRES_BELIEF_CONTENT",
                "hash-only evidence requires a separate non-sensitive atomic belief_content",
            )
        claim = observation.belief_content or prepared.payload or ""
        belief_id = self.dependencies.identity.new("belief")
        justification = (
            Justification(
                self.dependencies.identity.new("justification"),
                belief_id,
                observation.derived_from,
                "caller-supplied derived evidence",
            )
            if observation.derived_from
            else None
        )
        span = (
            (0, len(prepared.payload)) if prepared.payload and pramana is Pramana.SHABDA else None
        )
        validity = dict(observation.validity)
        correlation = dict(observation.correlation)
        if pramana is Pramana.PRATYAKSHA:
            validity.setdefault("tool_ok", correlation.get("status", "success") == "success")
            validity.setdefault("parsed", True)
            validity.setdefault("measured_only", True)
            validity.setdefault("environment_integrity", True)
        elif pramana is Pramana.SHABDA:
            validity.setdefault(
                "apta",
                {Integrity.TRUSTED: 0.9, Integrity.SEMI: 0.6, Integrity.UNTRUSTED: 0.3}[
                    source.integrity
                ],
            )
            validity.setdefault("assertive", True)
        premise_statuses = {
            identifier: item.status
            for identifier, item in self.store.get_beliefs(observation.derived_from).items()
            if item.episode_id == episode_id
        }
        belief = Belief(
            belief_id,
            episode_id,
            claim,
            normalize_content(claim),
            pramana,
            source.id,
            (EvidenceRef(evidence.id, span),),
            (justification,) if justification else (),
            {
                **dict(observation.qualifiers),
                "subject": observation.subject,
                "target": observation.target,
            },
            perishability,
            evidence.observed_at,
            stakes,
            Status.OUT,
            Status.OUT,
            validity={"schema_version": 1, "admitted_by": "public_api", **validity},
        )
        validation = validate_belief(
            belief,
            premise_statuses=premise_statuses,
            evidence_payloads={evidence.id: evidence.payload},
            evidence_mode=retention,
            max_words=int(self.config.data["ingestion"]["max_atomic_claim_words"]),
            max_chars=int(self.config.data["ingestion"]["max_atomic_claim_chars"]),
            yogyata_min_coverage=float(self.config.data["trust"]["yogyata"]["min_coverage"]),
            yogyata_min_recall=float(self.config.data["trust"]["yogyata"]["min_recall"]),
        )
        if not validation.valid:
            raise BeliefLedgerError("INVALID_BELIEF", "; ".join(validation.reasons))
        trust = determine_admission(
            belief,
            source,
            dict(self.config.data),
            episode_stakes=episode.default_stakes,
        )
        belief = replace(
            belief,
            normalized_content=validation.normalized_content,
            status=trust.status,
            admission_status=trust.status,
            validity={**dict(belief.validity), "checks": validation.checks},
        )
        support = IngestionSupport(
            self.dependencies.identity.new("support"),
            episode_id,
            belief.id,
            evidence.id,
            {"schema_version": 1, "content_hash": prepared.full_hash},
        )
        drafts.extend(
            (
                _record("EVIDENCE_INGESTED", "evidence", evidence.id, evidence),
                _record("BELIEF_ADMITTED", "belief", belief.id, belief),
                _record("INGESTION_SUPPORT_ADDED", "ingestion_support", support.id, support),
            )
        )
        if trust.method is not None and trust.status in {Status.PENDING, Status.QUARANTINED}:
            task = VerificationTask(
                self.dependencies.identity.new("verification"),
                episode_id,
                belief.id,
                trust.method,
                trust.k_required,
                max(1, trust.k_required),
            )
            drafts.append(_record("VERIFICATION_TASK_CREATED", "verification_task", task.id, task))
        if prepared.redacted:
            drafts.append(
                EventDraft(
                    "EVIDENCE_REDACTED",
                    "evidence",
                    evidence.id,
                    {"reason_code": "SECRET_LIKE_MATERIAL_REDACTED"},
                )
            )
        request_correlation = dict(observation.correlation)
        request_correlation["idempotency_fingerprint"] = content_hash(
            canonical_json(to_primitive(observation))
        )
        try:
            events = self.store.append_events(
                episode_id,
                drafts,
                correlation=request_correlation,
                idempotency_key=request_correlation.get("idempotency_key"),
            )
        except StoreError as exc:
            if "idempotency" in str(exc).casefold():
                raise BeliefLedgerError("IDEMPOTENCY_KEY_REUSED", str(exc)) from exc
            raise
        return _evidence_admission(events)

    def ingest_user_evidence(
        self, episode_id: str, content: str, *, sender: str, channel: str = ""
    ) -> EvidenceAdmission:
        return self.ingest_evidence(
            episode_id,
            EvidenceObservation.normalize(
                content,
                source_name=sender,
                source_kind="user",
                source_integrity="untrusted",
                provenance_root=f"user:{sender}",
                kind="user_message",
                correlation={"channel": channel},
                pramana="shabda",
            ),
        )

    def ingest_tool_result(self, episode_id: str, result: ToolResult) -> EvidenceAdmission:
        return self.ingest_evidence(
            episode_id,
            EvidenceObservation.normalize(
                result.content,
                source_name=result.name,
                source_kind="tool",
                source_integrity="semi",
                provenance_root=f"tool:{result.namespace}:{result.name}",
                kind="tool_result",
                correlation={"call_id": result.call_id or "", "status": result.status},
                belief_content=(
                    f"Tool {result.namespace + ':' if result.namespace else ''}{result.name} "
                    f"completed with status {result.status}"
                ),
            ),
        )

    def ingest_direct_observation(
        self, episode_id: str, observation: EvidenceObservation
    ) -> EvidenceAdmission:
        return self.ingest_evidence(episode_id, observation)

    def ingest_derived_evidence(
        self, episode_id: str, observation: EvidenceObservation
    ) -> EvidenceAdmission:
        if not observation.derived_from:
            raise BeliefLedgerError("MISSING_DERIVATION", "derived evidence requires premises")
        premises = self.store.get_beliefs(observation.derived_from)
        if any(
            identifier not in premises
            or premises[identifier].episode_id != episode_id
            or premises[identifier].status is not Status.IN
            for identifier in observation.derived_from
        ):
            raise BeliefLedgerError(
                "MISSING_ACTIVE_PREMISE", "every derived premise must be IN in this episode"
            )
        derived_pramana = (
            observation.pramana
            if observation.pramana in {"anumana", "arthapatti", "upamana"}
            else "anumana"
        )
        return self.ingest_evidence(episode_id, replace(observation, pramana=derived_pramana))

    def retract_evidence(self, episode_id: str, belief_id: str) -> NormalizedDecision:
        """Retract a supporting belief and proactively revoke every dependent permit."""

        self._episode(episode_id, require_active=True)
        belief = self.store.get_belief(belief_id)
        if belief is None or belief.episode_id != episode_id:
            raise BeliefLedgerError("BELIEF_NOT_FOUND", belief_id)
        drafts = [
            EventDraft(
                "BELIEF_STATUS_CHANGED",
                "belief",
                belief_id,
                {"from": belief.status.value, "to": "out", "cause": "caller_retraction"},
            )
        ]
        for support in self.store.list_supports(episode_id):
            if support.belief_id == belief_id and support.active:
                drafts.append(
                    EventDraft(
                        "INGESTION_SUPPORT_ACTIVITY_CHANGED",
                        "ingestion_support",
                        support.id,
                        {"active": False, "reason_code": "SUPPORT_RETRACTED"},
                    )
                )
        self.store.append_events(episode_id, drafts)
        self.enforcement.revoke_for_support(belief_id)
        return NormalizedDecision(1, "retracted", "SUPPORT_RETRACTED")

    def inventory(
        self, descriptors: Sequence[ToolDescriptor], *, complete: bool
    ) -> tuple[InventoryItem, ...]:
        return self.manifest.classify_inventory(tuple(descriptors), complete=complete)

    def record_approval(
        self, episode_id: str, approval: ApprovalResult, *, ttl_seconds: int = 300
    ) -> ApprovalReceipt | None:
        """Persist an exact approval binding supplied by a trusted control plane.

        The caller must authenticate the approving human or authority before constructing
        ``ApprovalResult``. This operation validates binding; it is not an authentication system
        and must not be exposed as a model-callable tool.
        """

        self._episode(episode_id, require_active=True)
        if type(approval.schema_version) is not int or approval.schema_version != 1:
            raise BeliefLedgerError("UNSUPPORTED_SCHEMA", "approval schema is unsupported")
        if approval.scope not in {"exact_action", "single_use"}:
            raise BeliefLedgerError("INVALID_APPROVAL_SCOPE", approval.scope)
        if re.fullmatch(r"[0-9a-f]{64}", approval.arguments_hash) is None:
            raise BeliefLedgerError("INVALID_ARGUMENTS_HASH", "expected a SHA-256 digest")
        policy = self.manifest.match(approval.tool_name, approval.namespace)
        if policy is None:
            raise BeliefLedgerError("NO_POLICY", approval.tool_name)
        if (approval.policy_id, approval.policy_revision) != (policy.id, policy.revision):
            raise BeliefLedgerError("APPROVAL_POLICY_MISMATCH", policy.id)
        binding = ApprovalBinding(
            1,
            episode_id,
            approval.context.stable_turn_id,
            approval.namespace,
            approval.tool_name,
            approval.arguments_hash,
            approval.target,
            policy.id,
            policy.revision,
            approval.scope,
        )
        return self.enforcement.issue_approval(
            binding, ttl_seconds=ttl_seconds, approved=approval.approved
        )

    def evaluate_action(
        self,
        episode_id: str,
        invocation: ToolInvocation,
        *,
        ttl_seconds: int = 30,
    ) -> ActionAuthorization:
        """Decide an action and, when allowed, issue an exact in-process permit."""

        self._episode(episode_id, require_active=True)
        if type(invocation.schema_version) is not int or invocation.schema_version != 1:
            raise BeliefLedgerError("UNSUPPORTED_SCHEMA", "tool invocation schema is unsupported")
        try:
            policy = self.manifest.match(invocation.name, invocation.namespace)
        except ValueError:
            return self._blocked_authorization(
                episode_id, invocation, "AMBIGUOUS_POLICY", "multiple policies match"
            )
        if policy is None:
            return self._evaluate_with_policy(episode_id, invocation, None, ttl_seconds)
        return self._evaluate_with_policy(episode_id, invocation, policy, ttl_seconds)

    def consume_permission(
        self, permit: ActionPermit, invocation: ToolInvocation
    ) -> PermissionConsumption:
        """Atomically consume a permit immediately before an adapter-owned dispatch."""

        if (
            type(permit.schema_version) is not int
            or permit.schema_version != 1
            or type(invocation.schema_version) is not int
            or invocation.schema_version != 1
        ):
            return PermissionConsumption(1, False, "UNSUPPORTED_SCHEMA", permit.decision_id)
        arguments = invocation.arguments_dict()
        try:
            policy = self.manifest.match(invocation.name, invocation.namespace)
        except ValueError:
            policy = None
        fields = policy.target_fields if policy else ()
        presented = replace(
            permit.binding,
            turn_id=invocation.context.stable_turn_id,
            namespace=invocation.namespace,
            tool_name=invocation.name,
            arguments_hash=content_hash(canonical_json(arguments)),
            target=_target(arguments, fields),
            policy_id=policy.id if policy else "",
            policy_revision=policy.revision if policy else "",
            canonicalization_version=policy.canonicalization_version if policy else 0,
            policy_content_digest=self.policy_digest,
            config_content_digest=self.config.digest,
            stakes=policy.base_stakes if policy else "",
        )
        result = self.enforcement.consume_action(permit._raw_token, presented)
        return PermissionConsumption(1, result.consumed, result.reason_code, permit.decision_id)

    def evaluate_output(self, episode_id: str, candidate: OutputCandidate) -> OutputEvaluation:
        """Evaluate bytes for delivery without claiming or invoking a host output sink."""

        self._episode(episode_id, require_active=True)
        if type(candidate.schema_version) is not int or candidate.schema_version != 1:
            raise BeliefLedgerError("UNSUPPORTED_SCHEMA", "output candidate schema is unsupported")
        if not isinstance(candidate.content, str) or not candidate.content.strip():
            raise BeliefLedgerError("INVALID_OUTPUT", "output content must be non-empty text")
        if not candidate.final:
            raise BeliefLedgerError("NON_FINAL_OUTPUT", "only final output candidates are accepted")
        try:
            stakes = Stakes(candidate.stakes)
        except ValueError as exc:
            raise BeliefLedgerError("INVALID_STAKES", candidate.stakes) from exc
        beliefs = self.store.list_beliefs(episode_id, statuses=(Status.IN, Status.PENDING))
        pending_marker = str(self.config.data["lint"]["pending_marker"])
        report = lint_response(
            candidate.content,
            beliefs,
            pending_marker=pending_marker,
            require_coverage=stakes in {Stakes.HIGH, Stakes.CRITICAL},
        )
        enforced = enforce_report(
            candidate.content,
            report,
            stakes=stakes,
            policy=dict(self.config.data["lint"]),
        )
        accepted = enforced.passed
        payload = (
            (enforced.replacement or candidate.content)
            if accepted
            else (enforced.replacement or "BLOCKED [OUTPUT_NOT_ACCEPTED]")
        )
        report_id = self.dependencies.identity.new("lint")
        self.store.append_events(
            episode_id,
            [
                EventDraft(
                    "LINT_RECORDED",
                    "lint_report",
                    report_id,
                    {
                        "response_hash": content_hash(candidate.content),
                        "passed": accepted,
                        "report": {
                            "id": report_id,
                            "reason_code": "OUTPUT_ACCEPTED" if accepted else "OUTPUT_BLOCKED",
                            "claims": to_primitive(enforced.claims),
                            "warnings": list(enforced.warnings),
                        },
                    },
                )
            ],
        )
        return OutputEvaluation(
            1,
            accepted,
            "OUTPUT_ACCEPTED" if accepted else "OUTPUT_BLOCKED",
            report_id,
            payload.encode("utf-8"),
        )

    def response_buffer(self, *, max_bytes: int = 1_048_576) -> ResponseGate:
        return ResponseGate(max_bytes=max_bytes, block_report="BLOCKED [OUTPUT_NOT_ACCEPTED]")

    def query(
        self,
        episode_id: str,
        text: str = "",
        *,
        limit: int = 20,
    ) -> tuple[dict[str, Any], ...]:
        self._episode(episode_id)
        words = set(normalize_content(text).split())
        retrieval_ids = self.store.fts_belief_ids(episode_id, text, limit=max(100, limit * 5))
        beliefs = (
            list(self.store.get_beliefs(retrieval_ids).values())
            if retrieval_ids
            else self.store.list_beliefs(episode_id, limit=max(100, limit * 5))
        )
        ranked = sorted(
            beliefs,
            key=lambda item: (-len(words & set(item.normalized_content.split())), item.id),
        )
        return tuple(to_primitive(item) for item in ranked[: max(1, min(limit, 100))])

    def explain_decision(self, episode_id: str, decision_id: str) -> DecisionExplanation:
        self._episode(episode_id)
        episode_events = self.store.events(episode_id)
        event = next(
            (
                item
                for item in episode_events
                if item.kind == "ACTION_AUTHORIZATION_EVALUATED"
                and item.aggregate_id == decision_id
            ),
            None,
        )
        if event is None:
            raise BeliefLedgerError("DECISION_NOT_FOUND", decision_id)
        payload = event.payload
        support_ids = tuple(str(item) for item in payload.get("supporting_belief_ids", []))
        conflict_ids = tuple(str(item) for item in payload.get("blocking_conflict_ids", []))
        beliefs = self.store.get_beliefs(support_ids)
        conflicts = {item.id: item for item in self.store.list_conflicts(episode_id, state=None)}
        policy = self.manifest.match(str(payload["tool_name"]), str(payload["namespace"]))
        state = self.enforcement.action_state(decision_id)
        return DecisionExplanation(
            1,
            decision_id,
            {
                "outcome": payload["outcome"],
                "reason_code": payload["reason_code"],
                "stakes": payload["stakes"],
            },
            asdict(policy) if policy else None,
            tuple(to_primitive(beliefs[item]) for item in support_ids if item in beliefs),
            tuple(to_primitive(conflicts[item]) for item in conflict_ids if item in conflicts),
            payload.get("approval_binding"),
            state or "decision_only",
            tuple(
                to_primitive(item) for item in episode_events if item.aggregate_id == decision_id
            ),
        )

    def verify_chain(self) -> ChainVerification:
        try:
            valid, reason = self.store.verify_hash_chain()
            enforcement_valid, enforcement_reason = self.enforcement.verify_hash_chain()
            projection_hashes = tuple(
                sorted(
                    {
                        **self.store.projection_hashes(),
                        "authorization_projection": self.enforcement.projection_hash(),
                    }.items()
                )
            )
        except Exception as exc:
            return ChainVerification(1, False, f"CHAIN_INVALID:{type(exc).__name__}", ())
        return ChainVerification(
            1,
            valid and enforcement_valid,
            canonical_json({"ledger": reason, "authorization": enforcement_reason}),
            projection_hashes,
        )

    def replay(self) -> ReplayResult:
        return self.store.replay()

    def list_episodes(self, *, limit: int = 100) -> tuple[Episode, ...]:
        return tuple(self.store.list_episodes(limit))

    def episode(self, episode_id: str) -> Episode:
        return self._episode(episode_id)

    def export_episode(self, episode_id: str) -> tuple[dict[str, Any], ...]:
        self._episode(episode_id)
        return tuple(to_primitive(event) for event in self.store.events(episode_id))

    def _evaluate_with_policy(
        self,
        episode_id: str,
        invocation: ToolInvocation,
        policy: ToolPolicy | None,
        ttl_seconds: int,
    ) -> ActionAuthorization:
        arguments = invocation.arguments_dict()
        if policy is None:
            registry_data: dict[str, Any] = {"schema_version": 1, "rules": []}
        else:
            registry_data = {
                "schema_version": 1,
                "rules": [
                    {
                        "id": policy.id,
                        "exact": [invocation.name],
                        "base_stakes": policy.base_stakes,
                        "effectful": policy.effectful,
                        "minimum_priority": policy.minimum_source_integrity,
                        "allow_human_approval": False,
                        "target_fields": list(policy.target_fields),
                        "preconditions": [
                            item
                            for item in policy.preconditions
                            if item != "explicit_user_confirmation"
                        ],
                    }
                ],
            }
        gate = ActionGate(self.store, dict(self.config.data), ActionPolicyRegistry(registry_data))
        decision = gate.evaluate(
            episode_id,
            invocation.name,
            arguments,
            description=invocation.description,
        )
        supports: tuple[str, ...] = ()
        conflicts: tuple[str, ...] = ()
        approval: ApprovalReceipt | None = None
        target = _target(arguments, policy.target_fields if policy else ())
        approval_binding: ApprovalBinding | None = None
        if policy is not None:
            supports, conflicts = self._support_and_conflicts(episode_id, invocation, policy)
            approval_binding = ApprovalBinding(
                1,
                episode_id,
                invocation.context.stable_turn_id,
                invocation.namespace,
                invocation.name,
                content_hash(canonical_json(arguments)),
                target,
                policy.id,
                policy.revision,
                "exact_action",
            )
            approval = self.enforcement.current_approval(approval_binding)
            approval_required = policy.approval_policy == "required" or (
                "explicit_user_confirmation" in policy.preconditions
            )
            if decision.outcome is GateOutcome.ALLOW and approval_required and approval is None:
                decision = GateDecision(
                    GateOutcome.APPROVE,
                    "APPROVAL_REQUIRED",
                    "A host-authenticated exact approval is required",
                    Stakes(policy.base_stakes),
                    (f"exact approval for {invocation.namespace}:{invocation.name}",),
                    "Confirm or deny through the host approval surface",
                    policy.id,
                )
        permit: ActionPermit | None = None
        decision_id = self.dependencies.identity.new("decision")
        if (
            decision.outcome is GateOutcome.ALLOW
            and policy is not None
            and policy.effectful
            and self.effective_profile is not EnforcementProfile.OBSERVE
        ):
            binding = ActionBinding(
                1,
                episode_id,
                invocation.context.stable_turn_id,
                invocation.namespace,
                invocation.name,
                content_hash(canonical_json(arguments)),
                target,
                policy.id,
                policy.revision,
                policy.canonicalization_version,
                self.policy_digest,
                self.config.digest,
                policy.base_stakes,
                supports,
                conflicts,
                approval.digest if approval else None,
            )
            try:
                issued = self.enforcement.issue_action(binding, ttl_seconds=ttl_seconds)
            except ValueError as exc:
                reason = str(exc)
                decision = GateDecision(
                    GateOutcome.BLOCK,
                    reason,
                    "Permission issuance failed closed",
                    Stakes(policy.base_stakes),
                )
            else:
                decision_id = issued.token_digest
                permit = ActionPermit(
                    1, issued.token_digest, binding, issued.expires_at, issued.token
                )
        elif decision.outcome is GateOutcome.ALLOW and policy is not None and policy.effectful:
            decision = GateDecision(
                GateOutcome.BLOCK,
                "PROFILE_DOES_NOT_ENFORCE_ACTIONS",
                "The effective observe profile cannot issue an action permit",
                Stakes(policy.base_stakes),
            )
        self._record_authorization(
            episode_id,
            decision_id,
            invocation,
            decision,
            policy,
            supports,
            conflicts,
            approval_binding if approval else None,
        )
        return ActionAuthorization(
            1,
            decision,
            decision_id,
            permit,
            supports,
            conflicts,
            self.policy_digest,
            self.config.digest,
        )

    def _blocked_authorization(
        self, episode_id: str, invocation: ToolInvocation, reason: str, message: str
    ) -> ActionAuthorization:
        decision = GateDecision(GateOutcome.BLOCK, reason, message, Stakes.HIGH)
        decision_id = self.dependencies.identity.new("decision")
        self._record_authorization(
            episode_id, decision_id, invocation, decision, None, (), (), None
        )
        return ActionAuthorization(
            1, decision, decision_id, None, (), (), self.policy_digest, self.config.digest
        )

    def _record_authorization(
        self,
        episode_id: str,
        decision_id: str,
        invocation: ToolInvocation,
        decision: GateDecision,
        policy: ToolPolicy | None,
        supports: tuple[str, ...],
        conflicts: tuple[str, ...],
        approval: ApprovalBinding | None,
    ) -> None:
        self.store.append_events(
            episode_id,
            [
                EventDraft(
                    "ACTION_AUTHORIZATION_EVALUATED",
                    "action_decision",
                    decision_id,
                    {
                        "tool_name": invocation.name,
                        "namespace": invocation.namespace,
                        "arguments_hash": content_hash(canonical_json(invocation.arguments_dict())),
                        "outcome": decision.outcome.value,
                        "reason_code": decision.reason_code,
                        "stakes": decision.stakes.value,
                        "policy_id": policy.id if policy else None,
                        "supporting_belief_ids": list(supports),
                        "blocking_conflict_ids": list(conflicts),
                        "approval_binding": asdict(approval) if approval else None,
                    },
                )
            ],
        )

    def _support_and_conflicts(
        self, episode_id: str, invocation: ToolInvocation, policy: ToolPolicy
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        beliefs = self.store.list_beliefs(episode_id)
        sources = {item.id: item for item in self.store.list_sources(episode_id)}
        open_conflicts = self.store.list_conflicts(episode_id)
        checks = resolve_preconditions(
            tuple(item for item in policy.preconditions if item != "explicit_user_confirmation"),
            action_name=invocation.name,
            args=invocation.arguments_dict(),
            target_fields=policy.target_fields,
            beliefs=beliefs,
            sources=sources,
            conflicts=open_conflicts,
            minimum_integrity=policy.minimum_source_integrity,
        )
        supports = tuple(sorted({item.belief_id for item in checks if item.belief_id}))
        target = _target(invocation.arguments_dict(), policy.target_fields)
        affected = tuple(
            sorted(
                item.id
                for item in open_conflicts
                if target == "the requested target"
                or target.casefold() in canonical_json(to_primitive(item)).casefold()
            )
        )
        return supports, affected

    def _supports_active(self, identifiers: tuple[str, ...]) -> bool:
        beliefs = self.store.get_beliefs(identifiers)
        return all(item in beliefs and beliefs[item].status is Status.IN for item in identifiers)

    def _conflicts_closed(self, episode_id: str, identifiers: tuple[str, ...]) -> bool:
        open_conflicts = self.store.list_conflicts(episode_id)
        if open_conflicts:
            return False
        if not identifiers:
            return True
        states = {
            item.id: item.state
            for item in self.store.list_conflicts(episode_id, state=None)
            if item.id in identifiers
        }
        return all(states.get(item) == "resolved" for item in identifiers)

    def _episode(self, episode_id: str, *, require_active: bool = False) -> Episode:
        episode = self.store.get_episode(episode_id)
        if episode is None:
            raise BeliefLedgerError("EPISODE_NOT_FOUND", episode_id)
        if require_active and episode.state != "active":
            raise BeliefLedgerError("EPISODE_FINALIZED", episode_id)
        return episode


def _record(kind: str, aggregate_type: str, aggregate_id: str, value: Any) -> EventDraft:
    return EventDraft(kind, aggregate_type, aggregate_id, {"record": to_primitive(value)})


def _target(arguments: Mapping[str, Any], fields: Sequence[str]) -> str:
    for field in fields:
        value = arguments.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "the requested target"


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(str(key)), dict):
            result[str(key)] = _merge(result[str(key)], value)
        else:
            result[str(key)] = copy.deepcopy(value)
    return result


def _configuration(
    root: Path, config: CoreConfig | CoreConfigSnapshot | Mapping[str, Any] | None
) -> CoreConfigSnapshot:
    defaults = yaml.safe_load(
        files("belief_ledger_core.data").joinpath("defaults.yaml").read_text()
    )
    if not isinstance(defaults, dict):
        raise RuntimeError("packaged core defaults are invalid")
    if isinstance(config, CoreConfigSnapshot):
        if config.state_root != root:
            raise BeliefLedgerError("STATE_ROOT_MISMATCH", str(config.state_root))
        try:
            validate_core_config(dict(config.data), defaults=defaults)
        except ValueError as exc:
            raise BeliefLedgerError("INVALID_CONFIG", str(exc)) from exc
        return config
    source: Path | None = None
    values: Mapping[str, Any] = {}
    if isinstance(config, CoreConfig):
        if config.schema_version != 1:
            raise BeliefLedgerError("UNSUPPORTED_SCHEMA", "core config schema is unsupported")
        source = config.explicit_path
        values = config.values or {}
        if source is not None:
            loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise BeliefLedgerError("INVALID_CONFIG", "configuration root must be an object")
            values = _merge(loaded, values)
    elif config is not None:
        values = config
    data = _merge(defaults, values)
    try:
        validate_core_config(data, defaults=defaults)
    except ValueError as exc:
        raise BeliefLedgerError("INVALID_CONFIG", str(exc)) from exc
    return CoreConfigSnapshot(1, root, source, data, "")


def _evidence_admission(events: Sequence[Any]) -> EvidenceAdmission:
    evidence_record: Mapping[str, Any] | None = None
    belief_record: Mapping[str, Any] | None = None
    for event in events:
        record = event.payload.get("record")
        if event.kind == "EVIDENCE_INGESTED" and isinstance(record, Mapping):
            evidence_record = record
        elif event.kind == "BELIEF_ADMITTED" and isinstance(record, Mapping):
            belief_record = record
    if evidence_record is None or belief_record is None:
        raise BeliefLedgerError(
            "ADMISSION_PROJECTION_MISSING", "idempotent admission is incomplete"
        )
    status = str(belief_record["status"])
    return EvidenceAdmission(
        1,
        str(evidence_record["id"]),
        str(belief_record["id"]),
        str(belief_record["source_id"]),
        status,
        "EVIDENCE_ADMITTED" if status == Status.IN.value else "EVIDENCE_RECORDED",
        bool(evidence_record.get("redacted", False)),
    )


def _manifest(
    manifest: ToolPolicyManifest | Mapping[str, Any] | None, *, mode: str
) -> ToolPolicyManifest:
    if isinstance(manifest, ToolPolicyManifest):
        return manifest
    if manifest is None:
        value = json.loads(
            files("belief_ledger_core.data").joinpath("tool-policies-v2.json").read_text()
        )
    else:
        value = dict(manifest)
    return ToolPolicyManifest.load(value, mode=mode)
