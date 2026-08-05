"""Safe inspection surface and complete-inventory upstream MCP proxy."""

from __future__ import annotations

from base64 import b32encode
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Any, Protocol, cast

from belief_ledger_core import (
    BeliefLedger,
    EpisodeContext,
    EvidenceObservation,
    ToolDescriptor,
    ToolInvocation,
    ToolResult,
)
from belief_ledger_core.events import canonical_json, content_hash, to_primitive
from belief_ledger_core.immutable import freeze


class McpMode(StrEnum):
    INSPECTION = "inspection"
    PROXY = "proxy"


@dataclass(frozen=True, slots=True)
class UpstreamTool:
    schema_version: int
    name: str
    description: str
    input_schema: dict[str, Any]
    namespace: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported upstream tool schema")
        if not all(isinstance(item, str) for item in (self.name, self.description, self.namespace)):
            raise ValueError("upstream tool names and descriptions must be strings")
        if not isinstance(self.input_schema, dict):
            raise ValueError("upstream tool input_schema must be an object")
        object.__setattr__(self, "input_schema", freeze(self.input_schema))

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor.create(
            self.name,
            self.input_schema,
            namespace=self.namespace,
            description=self.description,
        )


class UpstreamClient(Protocol):
    def list_tools(self) -> tuple[UpstreamTool, ...]: ...

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        namespace: str = "",
        correlation: dict[str, str],
    ) -> UpstreamCallResult: ...


@dataclass(frozen=True, slots=True)
class UpstreamCallResult:
    schema_version: int
    content: bytes
    is_error: bool
    status: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported upstream result schema")
        if not isinstance(self.content, bytes):
            raise ValueError("upstream result content must be bytes")
        if not isinstance(self.is_error, bool):
            raise ValueError("upstream result is_error must be a boolean")
        if not isinstance(self.status, str) or not self.status or len(self.status) > 128:
            raise ValueError("upstream result status is invalid")


@dataclass(frozen=True, slots=True)
class ProxyResult:
    schema_version: int
    forwarded: bool
    reason_code: str
    content: bytes = b""


def proxy_tool_name(namespace: str, name: str) -> str:
    """Return an injective MCP-safe name for one upstream namespace/name pair."""

    material = f"{namespace}\x00{name}".encode()
    return "belief_ledger_proxy_" + b32encode(material).decode("ascii").rstrip("=").lower()


class BeliefLedgerMcp:
    """MCP-facing application service with no model-controlled approval operation."""

    SAFE_TOOL_NAMES = (
        "belief_ledger_query",
        "belief_ledger_explain_decision",
        "belief_ledger_record_inference",
        "belief_ledger_verify_chain",
    )
    BYPASS_WARNING = (
        "Connecting directly to the upstream MCP server bypasses Belief Ledger proxy enforcement. "
        "MCP proxying does not own or gate final model-response delivery."
    )

    def __init__(
        self,
        ledger: BeliefLedger,
        *,
        mode: McpMode = McpMode.INSPECTION,
        upstream: UpstreamClient | None = None,
        inventory_complete: bool = False,
        allowed_episode_ids: tuple[str, ...] | None = None,
        max_upstream_bytes: int = 1_048_576,
        max_inventory_tools: int = 10_000,
        inventory_ttl_seconds: float = 0.0,
    ) -> None:
        self.ledger = ledger
        self.mode = mode
        self.upstream = upstream
        self.inventory_complete = inventory_complete
        self.allowed_episode_ids = (
            frozenset(allowed_episode_ids) if allowed_episode_ids is not None else None
        )
        if max_upstream_bytes <= 0:
            raise ValueError("max_upstream_bytes must be positive")
        if max_inventory_tools <= 0:
            raise ValueError("max_inventory_tools must be positive")
        self.max_upstream_bytes = max_upstream_bytes
        self.max_inventory_tools = max_inventory_tools
        if inventory_ttl_seconds < 0:
            raise ValueError("inventory_ttl_seconds must not be negative")
        self.inventory_ttl_seconds = inventory_ttl_seconds
        self._descriptors: dict[tuple[str, str], ToolDescriptor] = {}
        self._inventory_digest = ""
        self._inventory_reason = "INSPECTION_MODE"
        self._inventory_checked_at: float | None = None
        if mode is McpMode.PROXY:
            self.refresh_inventory()

    @property
    def capability_profile(self) -> str:
        return (
            "action_enforce"
            if self.mode is McpMode.PROXY
            and self._inventory_reason == "INVENTORY_VERIFIED"
            and self.ledger.effective_profile.value != "observe"
            else "observe"
        )

    @property
    def diagnostic(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": self.mode.value,
            "profile": self.capability_profile,
            "inventory_reason_code": self._inventory_reason,
            "bypass_warning": self.BYPASS_WARNING,
            "owns_final_output": False,
        }

    def _refresh_inventory_if_stale(self) -> None:
        """Re-verify the upstream inventory, at most once per TTL window.

        Drift detection costs an upstream `list_tools` round trip on every proxied call. The
        default TTL of 0 keeps that per-call verification, because it is what makes a schema
        change between inventory and dispatch impossible to miss. A deployment that has
        measured the round trip as the bottleneck may raise the TTL, accepting that a drifted
        schema can be dispatched against for up to that long. Trading the guarantee away is
        the caller's explicit decision, never the default.
        """

        now = monotonic()
        if (
            self._inventory_checked_at is not None
            and now - self._inventory_checked_at < self.inventory_ttl_seconds
        ):
            return
        self.refresh_inventory()

    def refresh_inventory(self) -> tuple[ToolDescriptor, ...]:
        self._inventory_checked_at = monotonic()
        if self.mode is not McpMode.PROXY or self.upstream is None:
            self._inventory_reason = "UPSTREAM_UNAVAILABLE"
            self._descriptors = {}
            return ()
        try:
            tools = tuple(self.upstream.list_tools())
        except Exception:
            self._inventory_reason = "UPSTREAM_FAILURE"
            self._descriptors = {}
            self._inventory_digest = ""
            return ()
        if len(tools) > self.max_inventory_tools:
            self._inventory_reason = "INVENTORY_TOO_LARGE"
            self._descriptors = {}
            self._inventory_digest = ""
            return ()
        if any(tool.name in self.SAFE_TOOL_NAMES for tool in tools):
            self._inventory_reason = "RESERVED_TOOL_NAME"
            self._descriptors = {}
            return ()
        try:
            descriptors = tuple(
                sorted(
                    (item.descriptor() for item in tools),
                    key=lambda item: (item.namespace, item.name),
                )
            )
            items = self.ledger.inventory(descriptors, complete=self.inventory_complete)
        except (TypeError, ValueError):
            self._inventory_reason = "INVALID_INVENTORY"
            self._descriptors = {}
            self._inventory_digest = ""
            return ()
        failures = [item for item in items if item.reason_code != "POLICY_MATCHED"]
        if failures:
            self._inventory_reason = failures[0].reason_code
            self._descriptors = {}
            self._inventory_digest = ""
            return ()
        self._descriptors = {
            (descriptor.namespace, descriptor.name): descriptor for descriptor in descriptors
        }
        self._inventory_digest = content_hash(
            canonical_json(
                [(item.namespace, item.name, item.schema_digest) for item in descriptors]
            )
        )
        self._inventory_reason = "INVENTORY_VERIFIED"
        return descriptors

    def exposed_tools(self) -> tuple[str, ...]:
        wrapped = (
            tuple(proxy_tool_name(namespace, name) for namespace, name in self._descriptors)
            if self.mode is McpMode.PROXY
            else ()
        )
        return (*self.SAFE_TOOL_NAMES, *sorted(wrapped))

    def wrapped_descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self._descriptors[key] for key in sorted(self._descriptors))

    def read_resource(self, uri: str) -> dict[str, Any]:
        if uri == "belief-ledger://capabilities":
            return self.diagnostic
        if uri == "belief-ledger://policies":
            return self.ledger.manifest.as_dict()
        prefix = "belief-ledger://episodes/"
        if not uri.startswith(prefix):
            raise ValueError("RESOURCE_NOT_FOUND")
        path = uri[len(prefix) :].split("/")
        episode_id = path[0]
        self._require_episode(episode_id)
        if len(path) == 2 and path[1] == "beliefs":
            return {"schema_version": 1, "beliefs": list(self.ledger.query(episode_id))}
        if len(path) == 2 and path[1] == "conflicts":
            return {
                "schema_version": 1,
                "conflicts": [
                    to_primitive(item)
                    for item in self.ledger.store.list_conflicts(episode_id, state=None)
                ],
            }
        if len(path) == 2 and path[1] == "audit":
            return {"schema_version": 1, "events": list(self.ledger.export_episode(episode_id))}
        if len(path) == 3 and path[1] == "decisions":
            return cast(
                dict[str, Any], to_primitive(self.ledger.explain_decision(episode_id, path[2]))
            )
        raise ValueError("RESOURCE_NOT_FOUND")

    def query(self, episode_id: str, text: str, *, limit: int = 20) -> tuple[dict[str, Any], ...]:
        self._require_episode(episode_id)
        return self.ledger.query(episode_id, text, limit=max(1, min(limit, 100)))

    def explain(self, episode_id: str, decision_id: str) -> dict[str, Any]:
        self._require_episode(episode_id)
        return cast(
            dict[str, Any], to_primitive(self.ledger.explain_decision(episode_id, decision_id))
        )

    def record_inference(
        self,
        episode_id: str,
        content: str,
        *,
        premise_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        self._require_episode(episode_id, require_active=True)
        observation = EvidenceObservation.normalize(
            content,
            source_name="mcp-model-inference",
            source_kind="model",
            source_integrity="untrusted",
            provenance_root="mcp:model-inference",
            kind="derived_inference",
            pramana="anumana",
            derived_from=premise_ids,
        )
        return cast(
            dict[str, Any],
            to_primitive(self.ledger.ingest_derived_evidence(episode_id, observation)),
        )

    def invoke(
        self,
        episode_id: str,
        context: EpisodeContext,
        name: str,
        arguments: dict[str, Any],
        *,
        namespace: str = "",
    ) -> ProxyResult:
        self._require_episode(episode_id, require_active=True)
        if self.mode is not McpMode.PROXY:
            return ProxyResult(1, False, "INSPECTION_MODE")
        if self.upstream is None or not self.inventory_complete:
            return ProxyResult(1, False, "INVENTORY_INCOMPLETE")
        before = self._inventory_digest
        self._refresh_inventory_if_stale()
        # Compare digests, not the descriptor tuple's truthiness: an upstream server that
        # legitimately exposes no tools produced an empty tuple and was reported as drift.
        if (
            not before
            or self._inventory_reason != "INVENTORY_VERIFIED"
            or self._inventory_digest != before
        ):
            return ProxyResult(1, False, "UPSTREAM_SCHEMA_DRIFT")
        descriptor = self._descriptors.get((namespace, name))
        if descriptor is None:
            return ProxyResult(1, False, "UNKNOWN_TOOL")
        try:
            descriptor.validate_arguments(arguments)
        except ValueError:
            return ProxyResult(1, False, "INVALID_ARGUMENTS")
        invocation = ToolInvocation.normalize(context, name, arguments, namespace=namespace)
        policy = self.ledger.manifest.match(name, namespace)
        if policy is None:
            return ProxyResult(1, False, "NO_POLICY")
        if policy.effectful:
            authorization = self.ledger.evaluate_action(episode_id, invocation)
            if authorization.permit is None:
                return ProxyResult(1, False, authorization.reason_code)
            consumed = self.ledger.consume_permission(authorization.permit, invocation)
            if not consumed.consumed:
                return ProxyResult(1, False, consumed.reason_code)
        try:
            correlation = {
                **dict(context.correlation),
                "session_id": context.persisted_session_id,
                "task_id": context.persisted_task_id,
                "turn_id": context.stable_turn_id,
            }
            upstream_result = self.upstream.call_tool(
                name,
                arguments,
                namespace=namespace,
                correlation=correlation,
            )
        except Exception:
            return ProxyResult(1, False, "UPSTREAM_FAILURE")
        if not isinstance(upstream_result, UpstreamCallResult):
            return ProxyResult(1, False, "UPSTREAM_RESULT_UNVERIFIED")
        if upstream_result.is_error:
            return ProxyResult(1, False, "UPSTREAM_REPORTED_ERROR")
        if len(upstream_result.content) > self.max_upstream_bytes:
            return ProxyResult(1, False, "UPSTREAM_RESULT_TOO_LARGE")
        evidence_text = upstream_result.content.decode("utf-8", errors="replace")
        try:
            self.ledger.ingest_tool_result(
                episode_id,
                ToolResult(1, context, namespace, name, evidence_text, upstream_result.status),
            )
        except Exception:
            return ProxyResult(
                1,
                True,
                "FORWARDED_EVIDENCE_REJECTED",
                upstream_result.content,
            )
        return ProxyResult(1, True, "FORWARDED", upstream_result.content)

    def _require_episode(self, episode_id: str, *, require_active: bool = False) -> None:
        if self.allowed_episode_ids is not None and episode_id not in self.allowed_episode_ids:
            raise ValueError("EPISODE_OUT_OF_SCOPE")
        episode = self.ledger.episode(episode_id)
        if require_active and episode.state != "active":
            raise ValueError("EPISODE_FINALIZED")
