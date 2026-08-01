from __future__ import annotations

from pathlib import Path

import pytest
from belief_ledger_core import (
    ApprovalResult,
    BeliefLedger,
    BeliefLedgerError,
    EnforcementProfile,
    EpisodeContext,
    EvidenceObservation,
    HostCapabilities,
    ToolInvocation,
)
from belief_ledger_core.events import canonical_json, content_hash
from belief_ledger_mcp import (
    BeliefLedgerMcp,
    McpMode,
    UpstreamCallResult,
    UpstreamTool,
    create_server,
)
from belief_ledger_mcp.cli import main as mcp_main
from belief_ledger_mcp.proxy import proxy_tool_name


class FakeUpstream:
    def __init__(self) -> None:
        self.tools = (
            UpstreamTool(1, "lookup", "lookup", {"type": "object"}, "crm"),
            UpstreamTool(1, "send", "send", {"type": "object"}, "crm"),
        )
        self.calls: list[str] = []
        self.correlations: list[dict[str, str]] = []
        self.result: object = UpstreamCallResult(
            1,
            b'{"ok":true,"password":"secret-value"}',
            False,
            "success",
        )

    def list_tools(self):
        return self.tools

    def call_tool(self, name, arguments, *, namespace="", correlation):
        self.calls.append(f"{namespace}:{name}")
        self.correlations.append(correlation)
        return self.result


def _proxy_ledger(state_root: Path, upstream: FakeUpstream) -> BeliefLedger:
    return BeliefLedger.open(
        state_root=state_root,
        manifest=_manifest(upstream),
        capabilities=HostCapabilities(pre_action_gate=True),
        requested_profile=EnforcementProfile.ACTION_ENFORCE,
    )


def _manifest(upstream: FakeUpstream) -> dict:
    lookup, send = (item.descriptor() for item in upstream.tools)
    return {
        "schema_version": 2,
        "rules": [
            {
                "id": "lookup",
                "revision": "v1",
                "effectful": False,
                "base_stakes": "low",
                "exact": ["lookup"],
                "namespace": "crm",
                "target_fields": [],
                "preconditions": [],
                "approval_policy": "none",
                "minimum_source_integrity": "untrusted",
                "canonicalization_version": 1,
                "input_schema_digest": lookup.schema_digest,
            },
            {
                "id": "send",
                "revision": "v1",
                "effectful": True,
                "base_stakes": "high",
                "exact": ["send"],
                "namespace": "crm",
                "target_fields": ["recipient"],
                "preconditions": [],
                "approval_policy": "required",
                "minimum_source_integrity": "trusted",
                "canonicalization_version": 1,
                "input_schema_digest": send.schema_digest,
            },
        ],
    }


def test_cli_refuses_an_unconfigured_proxy(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as refused:
        mcp_main(["--state-root", str(tmp_path), "--mode", "proxy"])
    assert refused.value.code == 2


def test_inspection_is_observe_and_has_no_privilege_escalation_tools(tmp_path: Path) -> None:
    ledger = BeliefLedger.open(state_root=tmp_path)
    context = EpisodeContext.normalize(session_id="inspection", turn_id="1")
    episode = ledger.start_episode(context)
    premise = ledger.ingest_evidence(
        episode.id,
        EvidenceObservation.normalize("A current fact", source_name="probe"),
    )
    decision = ledger.evaluate_action(
        episode.id, ToolInvocation.normalize(context, "read_item", {})
    )
    app = BeliefLedgerMcp(ledger, allowed_episode_ids=(episode.id,))
    assert app.capability_profile == "observe"
    assert "bypasses" in app.BYPASS_WARNING
    assert not any("approval" in name or "policy_mutation" in name for name in app.exposed_tools())
    assert app.read_resource("belief-ledger://capabilities")["owns_final_output"] is False
    assert app.read_resource(f"belief-ledger://episodes/{episode.id}/beliefs")["beliefs"]
    assert app.read_resource(f"belief-ledger://episodes/{episode.id}/conflicts")["conflicts"] == []
    assert app.read_resource(f"belief-ledger://episodes/{episode.id}/audit")["events"]
    assert (
        app.read_resource(
            f"belief-ledger://episodes/{episode.id}/decisions/{decision.decision_id}"
        )["decision_id"]
        == decision.decision_id
    )
    assert app.query(episode.id, "current")
    assert app.explain(episode.id, decision.decision_id)["decision_id"] == decision.decision_id
    inference = app.record_inference(episode.id, "A derived fact", premise_ids=(premise.belief_id,))
    assert inference["reason_code"] == "EVIDENCE_ADMITTED"
    with pytest.raises(ValueError, match="RESOURCE_NOT_FOUND"):
        app.read_resource("belief-ledger://missing")
    with pytest.raises(ValueError, match="EPISODE_OUT_OF_SCOPE"):
        app.read_resource("belief-ledger://episodes/episode-b/beliefs")
    assert type(create_server(app)).__name__ == "MCPServer"
    assert app.invoke(episode.id, context, "read", {}).reason_code == "INSPECTION_MODE"


@pytest.mark.anyio
async def test_official_sdk_exposes_only_safe_inspection_tools(
    tmp_path: Path,
) -> None:
    application = BeliefLedgerMcp(BeliefLedger.open(state_root=tmp_path))
    server = create_server(application)
    resources = await server.list_resources()
    assert {str(item.uri) for item in resources} == {
        "belief-ledger://capabilities",
        "belief-ledger://policies",
    }
    tools = await server.list_tools()
    assert {item.name for item in tools} == set(application.SAFE_TOOL_NAMES)
    verification = await server.call_tool("belief_ledger_verify_chain", {})
    assert verification


def test_proxy_blocks_then_consumes_and_preserves_upstream_bytes(tmp_path: Path) -> None:
    upstream = FakeUpstream()
    ledger = _proxy_ledger(tmp_path, upstream)
    context = EpisodeContext.normalize(session_id="s", turn_id="t")
    episode = ledger.start_episode(context)
    app = BeliefLedgerMcp(ledger, mode=McpMode.PROXY, upstream=upstream, inventory_complete=True)
    assert app.capability_profile == "action_enforce"
    read = app.invoke(episode.id, context, "lookup", {}, namespace="crm")
    assert read.forwarded and read.content.startswith(b'{"ok":true')
    assert (
        app.invoke(episode.id, context, "send", {"recipient": "42"}, namespace="crm").reason_code
        == "APPROVAL_REQUIRED"
    )
    invocation = ToolInvocation.normalize(
        context, "send", {"recipient": "42", "body": "hello"}, namespace="crm"
    )
    ledger.record_approval(
        episode.id,
        ApprovalResult(
            1,
            context,
            True,
            "crm",
            "send",
            content_hash(canonical_json(invocation.arguments_dict())),
            "42",
            "send",
            "v1",
            "exact_action",
        ),
    )
    assert (
        app.invoke(
            episode.id,
            context,
            "send",
            {"recipient": "43", "body": "hello"},
            namespace="crm",
        ).reason_code
        == "APPROVAL_REQUIRED"
    )
    assert (
        app.invoke(
            episode.id,
            context,
            "send",
            {"recipient": "42", "body": "changed"},
            namespace="crm",
        ).reason_code
        == "APPROVAL_REQUIRED"
    )
    result = app.invoke(
        episode.id,
        context,
        "send",
        {"recipient": "42", "body": "hello"},
        namespace="crm",
    )
    assert result.forwarded and result.content == b'{"ok":true,"password":"secret-value"}'
    assert upstream.calls == ["crm:lookup", "crm:send"]
    assert upstream.correlations[-1]["turn_id"] == "t"
    assert b"secret-value" not in (tmp_path / "ledger.sqlite3").read_bytes()
    assert (
        app.invoke(
            episode.id,
            context,
            "send",
            {"recipient": "42", "body": "hello"},
            namespace="crm",
        ).reason_code
        == "APPROVAL_REQUIRED"
    )
    assert upstream.calls == ["crm:lookup", "crm:send"]

    upstream.tools = (
        *upstream.tools[:-1],
        UpstreamTool(1, "send", "changed", {"type": "string"}, "crm"),
    )
    assert (
        app.invoke(episode.id, context, "send", {"recipient": "42"}, namespace="crm").reason_code
        == "UPSTREAM_SCHEMA_DRIFT"
    )


def test_proxy_inventory_and_upstream_failures_are_closed(tmp_path: Path) -> None:
    upstream = FakeUpstream()
    ledger = _proxy_ledger(tmp_path, upstream)
    context = EpisodeContext.normalize(session_id="s", turn_id="t")
    episode = ledger.start_episode(context)
    incomplete = BeliefLedgerMcp(
        ledger, mode=McpMode.PROXY, upstream=upstream, inventory_complete=False
    )
    assert incomplete.capability_profile == "observe"
    assert (
        incomplete.invoke(episode.id, context, "lookup", {}, namespace="crm").reason_code
        == "INVENTORY_INCOMPLETE"
    )
    complete = BeliefLedgerMcp(
        ledger, mode=McpMode.PROXY, upstream=upstream, inventory_complete=True
    )
    assert (
        complete.invoke(episode.id, context, "missing", {}, namespace="crm").reason_code
        == "UNKNOWN_TOOL"
    )

    class Broken(FakeUpstream):
        def call_tool(self, name, arguments, *, namespace="", correlation):
            raise RuntimeError("offline")

    broken_upstream = Broken()
    broken_ledger = _proxy_ledger(tmp_path / "broken", broken_upstream)
    broken_episode = broken_ledger.start_episode(context)
    broken = BeliefLedgerMcp(
        broken_ledger,
        mode=McpMode.PROXY,
        upstream=broken_upstream,
        inventory_complete=True,
    )
    assert (
        broken.invoke(broken_episode.id, context, "lookup", {}, namespace="crm").reason_code
        == "UPSTREAM_FAILURE"
    )

    reserved_upstream = FakeUpstream()
    reserved_upstream.tools = (UpstreamTool(1, "belief_ledger_query", "reserved", {}, ""),)
    reserved = BeliefLedgerMcp(
        BeliefLedger.open(state_root=tmp_path / "reserved"),
        mode=McpMode.PROXY,
        upstream=reserved_upstream,
        inventory_complete=True,
    )
    assert reserved.diagnostic["inventory_reason_code"] == "RESERVED_TOOL_NAME"

    class BrokenInventory(FakeUpstream):
        def list_tools(self):
            raise RuntimeError("offline")

    inventory_failure = BeliefLedgerMcp(
        BeliefLedger.open(state_root=tmp_path / "inventory-failure"),
        mode=McpMode.PROXY,
        upstream=BrokenInventory(),
        inventory_complete=True,
    )
    assert inventory_failure.capability_profile == "observe"
    assert inventory_failure.diagnostic["inventory_reason_code"] == "UPSTREAM_FAILURE"


def test_proxy_inventory_order_names_validation_and_episode_checks_are_safe(
    tmp_path: Path,
) -> None:
    assert proxy_tool_name("a_b", "c") != proxy_tool_name("a", "b_c")
    upstream = FakeUpstream()
    ledger = _proxy_ledger(tmp_path, upstream)
    context = EpisodeContext.normalize(session_id="s", turn_id="t")
    episode = ledger.start_episode(context)
    app = BeliefLedgerMcp(
        ledger,
        mode=McpMode.PROXY,
        upstream=upstream,
        inventory_complete=True,
    )
    upstream.tools = tuple(reversed(upstream.tools))
    reordered = app.invoke(episode.id, context, "lookup", {}, namespace="crm")
    assert reordered.forwarded and reordered.reason_code == "FORWARDED"

    upstream.calls.clear()
    with pytest.raises(BeliefLedgerError) as missing:
        app.invoke("missing-episode", context, "lookup", {}, namespace="crm")
    assert missing.value.reason_code == "EPISODE_NOT_FOUND"
    assert upstream.calls == []

    validating = FakeUpstream()
    validating.tools = (
        UpstreamTool(
            1,
            "lookup",
            "lookup",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "crm",
        ),
        UpstreamTool(1, "send", "send", {"type": "object"}, "crm"),
    )
    validating_ledger = _proxy_ledger(tmp_path / "validating", validating)
    validating_episode = validating_ledger.start_episode(context)
    validating_app = BeliefLedgerMcp(
        validating_ledger,
        mode=McpMode.PROXY,
        upstream=validating,
        inventory_complete=True,
    )
    invalid = validating_app.invoke(validating_episode.id, context, "lookup", {}, namespace="crm")
    assert invalid.reason_code == "INVALID_ARGUMENTS"
    assert validating.calls == []


@pytest.mark.parametrize(
    "result,max_bytes,reason_code",
    [
        (b"untyped", 100, "UPSTREAM_RESULT_UNVERIFIED"),
        (UpstreamCallResult(1, b"error", True, "error"), 100, "UPSTREAM_REPORTED_ERROR"),
        (UpstreamCallResult(1, b"oversized", False, "success"), 2, "UPSTREAM_RESULT_TOO_LARGE"),
    ],
)
def test_proxy_rejects_unverified_error_and_oversized_upstream_results(
    tmp_path: Path,
    result: object,
    max_bytes: int,
    reason_code: str,
) -> None:
    upstream = FakeUpstream()
    upstream.result = result
    ledger = _proxy_ledger(tmp_path, upstream)
    context = EpisodeContext.normalize(session_id="s", turn_id="t")
    episode = ledger.start_episode(context)
    app = BeliefLedgerMcp(
        ledger,
        mode=McpMode.PROXY,
        upstream=upstream,
        inventory_complete=True,
        max_upstream_bytes=max_bytes,
    )
    forwarded = app.invoke(episode.id, context, "lookup", {}, namespace="crm")
    assert not forwarded.forwarded and forwarded.reason_code == reason_code
