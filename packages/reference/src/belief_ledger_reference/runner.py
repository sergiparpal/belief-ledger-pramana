"""Deterministic standalone adapter with mandatory strict dispatch and delivery gates."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from belief_ledger_core.buffering import BufferResult, MemorySink, ResponseGate
from belief_ledger_core.contracts import (
    ApprovalResult,
    EnforcementProfile,
    EpisodeContext,
    HostCapabilities,
    NormalizedDecision,
    ToolInvocation,
)
from belief_ledger_core.dependencies import RuntimeDependencies, deterministic_dependencies
from belief_ledger_core.enforcement import (
    ActionBinding,
    ActionDecision,
    ApprovalBinding,
    ApprovalReceipt,
    EnforcementStore,
)
from belief_ledger_core.events import canonical_json, content_hash
from belief_ledger_core.runtime import LedgerRuntime

ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class DispatchPermit:
    schema_version: int
    decision: ActionDecision


@dataclass(frozen=True, slots=True)
class ReferenceAuthorization:
    schema_version: int
    outcome: str
    reason_code: str
    permit: DispatchPermit | None = None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DispatchResult:
    schema_version: int
    executed: bool
    reason_code: str
    value: Any = None


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    schema_version: int
    result: BufferResult
    deliveries: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class _ToolSpec:
    effectful: bool
    handler: ToolHandler


STRICT_CAPABILITIES = HostCapabilities(
    schema_version=1,
    per_request_context=True,
    pre_action_gate=True,
    atomic_action_token_consume=True,
    accepted_final_transform=True,
    exclusive_final_output_gate=True,
    buffered_stream_delivery=True,
    bound_approval=True,
    tool_inventory=True,
)


class ReferenceRunner:
    """Own the only public route to registered handlers and visible output."""

    def __init__(
        self,
        state_root: Path,
        *,
        dependencies: RuntimeDependencies | None = None,
        requested_profile: EnforcementProfile = EnforcementProfile.STRICT,
        max_buffer_bytes: int = 1_048_576,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.dependencies = dependencies or deterministic_dependencies()
        self.capabilities = STRICT_CAPABILITIES
        self.runtime = LedgerRuntime(
            self.state_root,
            self.dependencies,
            self.capabilities,
            requested_profile=requested_profile,
        )
        self.authorization = EnforcementStore(
            self.state_root / "authorization.sqlite3", self.dependencies
        )
        self.max_buffer_bytes = max_buffer_bytes
        self._tools: dict[tuple[str, str], _ToolSpec] = {}
        self._deployments: list[dict[str, Any]] = []
        self._deliveries: list[bytes] = []
        self._adapter_events: list[dict[str, Any]] = []
        self._active_supports: set[str] = set()
        self._latest_approval: ApprovalReceipt | None = None
        self.register_tool("health_probe", self._health_probe, effectful=False)
        self.register_tool("deploy", self._deploy, effectful=True)

    @property
    def deployments(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._deployments)

    @property
    def deliveries(self) -> tuple[bytes, ...]:
        return tuple(self._deliveries)

    def register_tool(
        self,
        name: str,
        handler: ToolHandler,
        *,
        namespace: str = "",
        effectful: bool,
    ) -> None:
        key = (namespace.strip(), name.strip())
        if not key[1] or key in self._tools:
            raise ValueError("tool name must be non-empty and unique within its namespace")
        self._tools[key] = _ToolSpec(effectful, handler)

    def tool_inventory(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "schema_version": 1,
                "namespace": namespace,
                "name": name,
                "effectful": spec.effectful,
            }
            for (namespace, name), spec in sorted(self._tools.items())
        )

    def start(self, context: EpisodeContext) -> str:
        return self.runtime.start_episode(context)

    def observe_health(self, value: str) -> NormalizedDecision:
        decision = self.runtime.ingest_health(value)
        normalized = value.casefold().strip()
        if normalized == "green":
            self._active_supports.add("health:production=green")
        elif normalized == "red":
            self._active_supports.discard("health:production=green")
            self.authorization.revoke_for_support("health:production=green")
        return decision

    def approve_deployment(
        self,
        invocation: ToolInvocation,
        *,
        approved: bool = True,
        ttl_seconds: int = 60,
    ) -> ApprovalReceipt | None:
        arguments = invocation.arguments_dict()
        arguments_hash = content_hash(canonical_json(arguments))
        target = str(arguments.get("environment", ""))
        binding = ApprovalBinding(
            1,
            self.runtime.episode_id,
            invocation.context.stable_turn_id,
            invocation.namespace,
            invocation.name,
            arguments_hash,
            target,
            "deploy-production",
            "sha256:fixture-policy-v1",
            "exact_action",
        )
        receipt = self.authorization.issue_approval(
            binding, ttl_seconds=ttl_seconds, approved=approved
        )
        self.runtime.record_approval(
            ApprovalResult(
                1,
                invocation.context,
                approved,
                invocation.namespace,
                invocation.name,
                arguments_hash,
                target,
                binding.policy_id,
                binding.policy_revision,
                binding.scope,
            )
        )
        self._latest_approval = receipt
        return receipt

    def authorize(
        self,
        invocation: ToolInvocation,
        *,
        approval: ApprovalReceipt | None = None,
        ttl_seconds: int = 30,
    ) -> ReferenceAuthorization:
        spec = self._tools.get((invocation.namespace, invocation.name))
        if spec is None:
            return ReferenceAuthorization(1, "block", "UNKNOWN_TOOL")
        if not spec.effectful:
            return ReferenceAuthorization(1, "allow", "READ_ONLY_EXPLICIT")
        decision = self.runtime.authorize_deployment(invocation)
        if decision.outcome != "allow":
            return ReferenceAuthorization(
                1, decision.outcome, decision.reason_code, missing=decision.missing
            )
        arguments = invocation.arguments_dict()
        selected_approval = approval or self._latest_approval
        binding = ActionBinding(
            1,
            self.runtime.episode_id,
            invocation.context.stable_turn_id,
            invocation.namespace,
            invocation.name,
            content_hash(canonical_json(arguments)),
            str(arguments.get("environment", "")),
            "deploy-production",
            "sha256:fixture-policy-v1",
            1,
            content_hash("deploy-production|sha256:fixture-policy-v1"),
            content_hash("reference-runner|strict|v1"),
            "critical",
            ("health:production=green",),
            (),
            selected_approval.digest if selected_approval else None,
        )
        try:
            action = self.authorization.issue_action(binding, ttl_seconds=ttl_seconds)
        except ValueError as exc:
            return ReferenceAuthorization(1, "block", str(exc))
        return ReferenceAuthorization(
            1,
            "allow",
            decision.reason_code,
            DispatchPermit(1, action),
        )

    def dispatch(
        self, invocation: ToolInvocation, permit: DispatchPermit | None = None
    ) -> DispatchResult:
        spec = self._tools.get((invocation.namespace, invocation.name))
        if spec is None:
            return DispatchResult(1, False, "UNKNOWN_TOOL")
        if spec.effectful:
            if permit is None:
                return DispatchResult(1, False, "TOKEN_REQUIRED")
            arguments = invocation.arguments_dict()
            presented = replace(
                permit.decision.binding,
                episode_id=self.runtime.episode_id,
                turn_id=invocation.context.stable_turn_id,
                namespace=invocation.namespace,
                tool_name=invocation.name,
                arguments_hash=content_hash(canonical_json(arguments)),
                target=str(arguments.get("environment", "")),
            )
            consumed = self.authorization.consume_action(
                permit.decision.token,
                presented,
                support_is_active=lambda identifiers: set(identifiers).issubset(
                    self._active_supports
                ),
                conflicts_are_closed=lambda identifiers: not identifiers,
            )
            if not consumed.consumed:
                return DispatchResult(1, False, consumed.reason_code)
        try:
            return DispatchResult(1, True, "DISPATCHED", spec.handler(invocation.arguments_dict()))
        except Exception as exc:
            return DispatchResult(1, False, "HANDLER_ERROR", type(exc).__name__)

    def deliver_output(
        self,
        chunks: Iterable[str | bytes],
        *,
        lint: Callable[[str], bool],
        stakes: str = "critical",
    ) -> DeliveryOutcome:
        del stakes  # This strict adapter safely buffers every output class.
        self._record_adapter_event("OUTPUT_BUFFER_STARTED", {"max_bytes": self.max_buffer_bytes})
        gate = ResponseGate(
            max_bytes=self.max_buffer_bytes,
            block_report="BLOCKED [OUTPUT_NOT_ACCEPTED]",
        )
        for index, chunk in enumerate(chunks):
            gate.append(index, chunk)
        sink = MemorySink()
        result = gate.finalize(lint, sink)
        self._deliveries.extend(sink.deliveries)
        self._record_adapter_event(
            "OUTPUT_BUFFER_ACCEPTED" if result.accepted else "OUTPUT_BUFFER_DISCARDED",
            {
                "reason_code": result.reason_code,
                "delivered_bytes": result.delivered_bytes,
            },
        )
        return DeliveryOutcome(1, result, tuple(sink.deliveries))

    def normalized_events(self) -> tuple[dict[str, Any], ...]:
        runtime_events = tuple(event.as_dict() for event in self.runtime.events)
        return (*runtime_events, *self.authorization.events(), *self._adapter_events)

    def _record_adapter_event(self, kind: str, payload: dict[str, Any]) -> None:
        self._adapter_events.append(
            {
                "schema_version": 2,
                "payload_schema_version": 1,
                "id": self.dependencies.identity.new("event"),
                "at": self.dependencies.clock.now().isoformat().replace("+00:00", "Z"),
                "kind": kind,
                "payload": {"payload_schema_version": 1, **payload},
            }
        )

    def _health_probe(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"environment": arguments.get("environment", "production"), "observed": True}

    def _deploy(self, arguments: dict[str, Any]) -> dict[str, Any]:
        deployment = {"ordinal": len(self._deployments) + 1, **arguments}
        self._deployments.append(deployment)
        return dict(deployment)


def invocation_from_mapping(
    context: EpisodeContext,
    value: Mapping[str, Any],
) -> ToolInvocation:
    arguments = value.get("arguments", {})
    if not isinstance(arguments, Mapping):
        raise ValueError("arguments must be an object")
    return ToolInvocation.normalize(
        context,
        str(value.get("name", "")),
        dict(arguments),
        namespace=str(value.get("namespace", "")),
    )
