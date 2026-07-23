"""Host-neutral runtime facade used by adapters and deterministic examples."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    ApprovalResult,
    EnforcementProfile,
    EpisodeContext,
    HostCapabilities,
    NormalizedDecision,
    ToolInvocation,
    negotiate_profile,
)
from .dependencies import RuntimeDependencies
from .events import canonical_json, content_hash, isoformat_utc


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    schema_version: int
    payload_schema_version: int
    id: str
    episode_id: str
    at: str
    kind: str
    payload: tuple[tuple[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "payload_schema_version": self.payload_schema_version,
            "id": self.id,
            "episode_id": self.episode_id,
            "at": self.at,
            "kind": self.kind,
            "payload": dict(self.payload),
        }


class CapabilityShortfall(RuntimeError):
    pass


class LedgerRuntime:
    """Host-neutral normalized lifecycle with explicit dependencies and capabilities."""

    def __init__(
        self,
        state_root: Path,
        dependencies: RuntimeDependencies,
        capabilities: HostCapabilities,
        *,
        requested_profile: EnforcementProfile = EnforcementProfile.OBSERVE,
        allow_diagnostic_downgrade: bool = False,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.dependencies = dependencies
        self.capabilities = capabilities
        self.requested_profile = requested_profile
        self.profile_selection = negotiate_profile(
            capabilities,
            requested_profile,
            allow_diagnostic_downgrade=allow_diagnostic_downgrade,
        )
        if (
            self.profile_selection.missing
            and requested_profile is not EnforcementProfile.OBSERVE
            and not allow_diagnostic_downgrade
        ):
            raise CapabilityShortfall(
                f"CAPABILITY_SHORTFALL:{requested_profile.value}:"
                f"{','.join(self.profile_selection.missing)}"
            )
        self.effective_profile = self.profile_selection.effective
        self._episode_id = ""
        self._context: EpisodeContext | None = None
        self._events: list[RuntimeEvent] = []
        self._health = "missing"
        self._green_support_active = False
        self._approval: ApprovalResult | None = None

    @property
    def episode_id(self) -> str:
        if not self._episode_id:
            raise RuntimeError("episode has not started")
        return self._episode_id

    @property
    def events(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)

    def start_episode(self, context: EpisodeContext) -> str:
        if context.schema_version != 1:
            raise ValueError("unsupported episode context schema")
        if self._episode_id:
            return self._episode_id
        self._context = context
        self._episode_id = self.dependencies.identity.new("episode")
        self._record(
            "HOST_CAPABILITIES_RECORDED",
            {
                "capabilities": {
                    name: getattr(self.capabilities, name)
                    for name in self.capabilities.__dataclass_fields__
                },
                "context": {
                    "session_id": context.persisted_session_id,
                    "task_id": context.persisted_task_id,
                    "turn_id": context.stable_turn_id,
                    "platform": context.platform,
                    "model": context.model,
                    "correlation": list(context.correlation),
                },
            },
        )
        self._record(
            "ENFORCEMENT_PROFILE_SELECTED",
            {
                "requested": self.requested_profile.value,
                "effective": self.effective_profile.value,
                "missing": list(self.profile_selection.missing),
                "reason_codes": list(self.profile_selection.reason_codes),
                "downgraded": self.profile_selection.downgraded,
            },
        )
        return self._episode_id

    def ingest_health(self, value: str) -> NormalizedDecision:
        normalized = value.casefold().strip()
        if normalized not in {"green", "red"}:
            raise ValueError("health fixture must be green or red")
        if normalized == "green":
            self._health = "green"
            self._green_support_active = True
            outcome = "observed"
        else:
            self._health = "red"
            was_active = self._green_support_active
            self._green_support_active = False
            outcome = "retracted" if was_active else "observed"
        self._record(
            "EVIDENCE_INGESTED",
            {"source": "health_probe", "target": "production", "value": normalized},
        )
        if outcome == "retracted":
            self._record(
                "SUPPORT_RETRACTED",
                {"defeated": "health:production=green", "by": "health:production=red"},
            )
        return NormalizedDecision(1, outcome, "EVIDENCE_RECORDED")

    def record_approval(self, approval: ApprovalResult) -> NormalizedDecision:
        if not approval.approved:
            self._record("APPROVAL_RECEIPT_DENIED", {"tool": approval.tool_name})
            return NormalizedDecision(1, "block", "APPROVAL_DENIED")
        self._approval = approval
        self._record(
            "APPROVAL_RECEIPT_ISSUED",
            {
                "tool": approval.tool_name,
                "namespace": approval.namespace,
                "arguments_hash": approval.arguments_hash,
                "target": approval.target,
                "policy_id": approval.policy_id,
                "policy_revision": approval.policy_revision,
                "scope": approval.scope,
                "turn_id": approval.context.stable_turn_id,
            },
        )
        return NormalizedDecision(1, "approved", "APPROVAL_RECORDED")

    def authorize_deployment(self, invocation: ToolInvocation) -> NormalizedDecision:
        arguments = invocation.arguments_dict()
        target = str(arguments.get("environment", ""))
        missing: list[str] = []
        if not self._green_support_active:
            missing.append("production health is green")
        arguments_hash = content_hash(canonical_json(arguments))
        approval = self._approval
        approval_valid = bool(
            approval
            and approval.approved
            and approval.context.stable_turn_id == invocation.context.stable_turn_id
            and approval.namespace == invocation.namespace
            and approval.tool_name == invocation.name
            and approval.arguments_hash == arguments_hash
            and approval.target == target
            and approval.policy_id == "deploy-production"
            and approval.policy_revision == "sha256:fixture-policy-v1"
            and approval.scope == "exact_action"
        )
        if not approval_valid:
            missing.append("exact human approval")
        if missing:
            if not self._green_support_active and self._health == "red":
                reason = "SUPPORT_RETRACTED"
            elif missing == ["exact human approval"]:
                reason = "APPROVAL_REQUIRED"
            else:
                reason = "MISSING_PRECONDITION"
            decision = NormalizedDecision(
                1,
                "block",
                reason,
                tuple(missing),
                (
                    "Observe current production health with health_probe"
                    if "production health is green" in missing
                    else None
                ),
            )
        else:
            decision = NormalizedDecision(1, "allow", "PRECONDITIONS_SATISFIED")
        self._record(
            "ACTION_AUTHORIZATION_DECIDED",
            {
                "tool": invocation.name,
                "namespace": invocation.namespace,
                "arguments_hash": arguments_hash,
                "target": target,
                "outcome": decision.outcome,
                "reason_code": decision.reason_code,
                "missing": list(decision.missing),
            },
        )
        return decision

    def normalized_events_json(self) -> str:
        return canonical_json([event.as_dict() for event in self._events])

    def _record(self, kind: str, payload: dict[str, Any]) -> None:
        self._events.append(
            RuntimeEvent(
                2,
                1,
                self.dependencies.identity.new("event"),
                self.episode_id,
                isoformat_utc(self.dependencies.clock.now()),
                kind,
                tuple(sorted(payload.items())),
            )
        )
