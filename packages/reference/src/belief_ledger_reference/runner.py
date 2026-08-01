"""Deterministic strict adapter with caller-defined tools, policies, and handlers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from belief_ledger_core import (
    ActionPermit,
    ApprovalResult,
    BeliefLedger,
    EnforcementProfile,
    EpisodeContext,
    EvidenceAdmission,
    EvidenceObservation,
    HostCapabilities,
    RuntimeDependencies,
    ToolDescriptor,
    ToolInvocation,
    ToolPolicyManifest,
    deterministic_dependencies,
)
from belief_ledger_core.api_types import ActionAuthorization
from belief_ledger_core.buffering import BufferResult, MemorySink, ResponseGate
from belief_ledger_core.events import to_primitive
from belief_ledger_core.manifest import ToolPolicy
from belief_ledger_core.models import Stakes

ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class DispatchPermit:
    schema_version: int
    permit: ActionPermit


@dataclass(frozen=True, slots=True)
class ReferenceAuthorization:
    schema_version: int
    outcome: str
    reason_code: str
    permit: DispatchPermit | None = None
    missing: tuple[str, ...] = ()
    decision_id: str = ""


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
    """Own the only public route to a private caller-registered handler registry and sink."""

    def __init__(
        self,
        state_root: Path,
        *,
        dependencies: RuntimeDependencies | None = None,
        requested_profile: EnforcementProfile = EnforcementProfile.STRICT,
        max_buffer_bytes: int = 1_048_576,
        manifest: ToolPolicyManifest | Mapping[str, Any] | None = None,
    ) -> None:
        requested_root = state_root.expanduser().absolute()
        if requested_root.is_symlink():
            raise ValueError("reference state_root must not be a symbolic link")
        self.state_root = requested_root.resolve()
        self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.dependencies = dependencies or deterministic_dependencies()
        self.capabilities = STRICT_CAPABILITIES
        self.requested_profile = requested_profile
        self.max_buffer_bytes = max_buffer_bytes
        self._base_manifest = manifest
        self._rules: list[dict[str, Any]] = []
        self._descriptors: dict[tuple[str, str], ToolDescriptor] = {}
        self._effectful: dict[tuple[str, str], bool] = {}
        self._handlers: dict[tuple[str, str], ToolHandler] = {}
        self._ledger: BeliefLedger | None = None
        self._episode_id = ""
        self._context: EpisodeContext | None = None
        self._deliveries: list[bytes] = []
        self._adapter_events: list[dict[str, Any]] = []

    @property
    def ledger(self) -> BeliefLedger:
        if self._ledger is None:
            raise RuntimeError("reference episode has not started")
        return self._ledger

    @property
    def runtime(self) -> BeliefLedger:
        """Compatibility alias for callers that previously inspected ``runtime``."""

        return self.ledger

    @property
    def deliveries(self) -> tuple[bytes, ...]:
        return tuple(self._deliveries)

    def register_tool(
        self,
        descriptor: ToolDescriptor | str,
        handler: ToolHandler,
        *,
        effectful: bool,
        policy: ToolPolicy | Mapping[str, Any] | None = None,
        namespace: str = "",
        input_schema: Mapping[str, Any] | None = None,
        description: str = "",
    ) -> None:
        """Register one private handler before starting the runner.

        A policy must either be supplied here or already exist in the constructor manifest. The
        declared effect classification must agree with that policy.
        """

        if self._ledger is not None:
            raise ValueError("tool registration closes when the episode starts")
        item = (
            ToolDescriptor.create(
                descriptor,
                input_schema or {},
                namespace=namespace,
                description=description,
            )
            if isinstance(descriptor, str)
            else descriptor
        )
        key = (item.namespace, item.name)
        if not item.name or key in self._handlers:
            raise ValueError("tool name must be non-empty and unique within its namespace")
        normalized: ToolPolicy | None = None
        if policy is not None:
            if isinstance(policy, Mapping):
                value = dict(policy)
                value.setdefault("exact", [item.name])
                value.setdefault("namespace", item.namespace or None)
                value.setdefault("input_schema_digest", item.schema_digest)
                normalized = ToolPolicyManifest.load({"schema_version": 2, "rules": [value]}).rules[
                    0
                ]
            else:
                normalized = policy
            self._rules.append(asdict(normalized))
        elif self._base_manifest is not None:
            candidate = (
                ToolPolicyManifest.load(self._base_manifest)
                if isinstance(self._base_manifest, Mapping)
                else self._base_manifest
            )
            normalized = candidate.match(item.name, item.namespace)
        if normalized is None:
            raise ValueError("registered tools require an explicit matching policy")
        if normalized.effectful is not effectful:
            raise ValueError("tool effect classification disagrees with its policy")
        if normalized.input_schema_digest and normalized.input_schema_digest != item.schema_digest:
            raise ValueError("tool schema disagrees with its policy digest")
        self._descriptors[key] = item
        self._effectful[key] = effectful
        self._handlers[key] = handler

    def tool_inventory(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                **to_primitive(self._descriptors[key]),
                "effectful": self._effectful[key],
            }
            for key in sorted(self._descriptors)
        )

    def start(self, context: EpisodeContext) -> str:
        if self._ledger is not None:
            if context != self._context:
                raise ValueError("reference runner is already bound to a different context")
            return self._episode_id
        manifest = self._compiled_manifest()
        inventory = manifest.classify_inventory(tuple(self._descriptors.values()), complete=True)
        if any(item.reason_code != "POLICY_MATCHED" for item in inventory):
            reasons = ",".join(item.reason_code for item in inventory)
            raise ValueError(f"reference inventory is not policy-complete: {reasons}")
        self._ledger = BeliefLedger.open(
            state_root=self.state_root,
            dependencies=self.dependencies,
            capabilities=self.capabilities,
            requested_profile=self.requested_profile,
            manifest=manifest,
        )
        self._episode_id = self._ledger.start_episode(context).id
        self._context = context
        return self._episode_id

    def ingest_evidence(self, observation: EvidenceObservation) -> EvidenceAdmission:
        return self.ledger.ingest_evidence(self._episode_id, observation)

    def retract_support(self, belief_id: str) -> str:
        return self.ledger.retract_evidence(self._episode_id, belief_id).reason_code

    def record_approval(self, approval: ApprovalResult, *, ttl_seconds: int = 300) -> str:
        receipt = self.ledger.record_approval(self._episode_id, approval, ttl_seconds=ttl_seconds)
        return "APPROVAL_RECORDED" if receipt else "APPROVAL_DENIED"

    def authorize(
        self,
        invocation: ToolInvocation,
        *,
        ttl_seconds: int = 30,
    ) -> ReferenceAuthorization:
        authorization: ActionAuthorization = self.ledger.evaluate_action(
            self._episode_id, invocation, ttl_seconds=ttl_seconds
        )
        return ReferenceAuthorization(
            1,
            authorization.outcome,
            authorization.reason_code,
            DispatchPermit(1, authorization.permit) if authorization.permit else None,
            authorization.decision.missing,
            authorization.decision_id,
        )

    def dispatch(
        self, invocation: ToolInvocation, permit: DispatchPermit | None = None
    ) -> DispatchResult:
        key = (invocation.namespace, invocation.name)
        effectful = self._effectful.get(key)
        if effectful is None:
            return DispatchResult(1, False, "UNKNOWN_TOOL")
        try:
            self._descriptors[key].validate_arguments(invocation.arguments_dict())
        except ValueError:
            return DispatchResult(1, False, "INVALID_ARGUMENTS")
        if effectful:
            if permit is None:
                return DispatchResult(1, False, "TOKEN_REQUIRED")
            consumed = self.ledger.consume_permission(permit.permit, invocation)
            if not consumed.consumed:
                return DispatchResult(1, False, consumed.reason_code)
        # Handler lookup deliberately occurs after successful atomic consumption.
        handler = self._handlers.get(key)
        if handler is None:
            return DispatchResult(1, False, "HANDLER_UNAVAILABLE")
        try:
            return DispatchResult(1, True, "DISPATCHED", handler(invocation.arguments_dict()))
        except Exception as exc:
            return DispatchResult(1, False, "HANDLER_ERROR", type(exc).__name__)

    def deliver_output(
        self,
        chunks: Iterable[str | bytes],
        *,
        lint: Callable[[str], bool],
        stakes: str = "critical",
    ) -> DeliveryOutcome:
        try:
            selected_stakes = Stakes(stakes)
        except ValueError as exc:
            raise ValueError("output stakes are invalid") from exc
        self._record_adapter_event(
            "OUTPUT_BUFFER_STARTED",
            {"max_bytes": self.max_buffer_bytes, "stakes": selected_stakes.value},
        )
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
            {"reason_code": result.reason_code, "delivered_bytes": result.delivered_bytes},
        )
        return DeliveryOutcome(1, result, tuple(sink.deliveries))

    def normalized_events(self) -> tuple[dict[str, Any], ...]:
        return (
            *self.ledger.export_episode(self._episode_id),
            *self.ledger.enforcement.events(),
            *self._adapter_events,
        )

    def _compiled_manifest(self) -> ToolPolicyManifest:
        if self._base_manifest is None:
            return ToolPolicyManifest.load({"schema_version": 2, "rules": self._rules})
        base = (
            ToolPolicyManifest.load(self._base_manifest)
            if isinstance(self._base_manifest, Mapping)
            else self._base_manifest
        )
        return ToolPolicyManifest.load(
            {"schema_version": 2, "rules": [*base.as_dict()["rules"], *self._rules]}
        )

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
