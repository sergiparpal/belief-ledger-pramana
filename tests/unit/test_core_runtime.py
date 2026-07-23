from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from belief_ledger_pramana.contracts import (
    ApprovalResult,
    EnforcementProfile,
    EpisodeContext,
    HostCapabilities,
    ToolInvocation,
)
from belief_ledger_pramana.core_config import load_core_config
from belief_ledger_pramana.core_runtime import CapabilityShortfall, LedgerRuntime
from belief_ledger_pramana.dependencies import (
    CallableStructuredModel,
    FakeStructuredModel,
    FixedClock,
    FixedMonotonicClock,
    SequenceIdentity,
    SequenceToken,
    StructuredModelProviderError,
    StructuredModelRequest,
    StructuredModelResult,
    deterministic_dependencies,
)
from belief_ledger_pramana.events import canonical_json, content_hash
from examples.deployment_gate.run import run_fake


def _context() -> EpisodeContext:
    return EpisodeContext.normalize(
        session_id="session",
        turn_id="turn",
        task_id="task",
        platform="fake",
        model="none",
        correlation={"z": 2, "a": 1, "empty": ""},
    )


def _strict_capabilities() -> HostCapabilities:
    return HostCapabilities(
        1,
        per_request_context=True,
        pre_action_gate=True,
        atomic_action_token_consume=True,
        accepted_final_transform=True,
        exclusive_final_output_gate=True,
        buffered_stream_delivery=True,
        bound_approval=True,
        tool_inventory=True,
    )


def test_deployment_runner_executes_core_in_process() -> None:
    expected = json.loads(
        Path("examples/deployment_gate/expected-result-v1.json").read_text(encoding="utf-8")
    )
    assert run_fake() == expected


def test_context_normalization_and_tool_values_are_immutable() -> None:
    context = _context()
    assert context.correlation == (("a", "1"), ("z", "2"))
    assert context.persisted_session_id == "session"
    assert context.persisted_task_id == "task"
    assert context.stable_turn_id == "turn"
    missing = EpisodeContext.normalize()
    assert missing.persisted_session_id == "unidentified-session"
    assert missing.persisted_task_id == "unidentified-task"
    assert missing.stable_turn_id == "unidentified-turn"
    invocation = ToolInvocation.normalize(context, " deploy ", {"z": 2, "a": 1})
    assert invocation.name == "deploy"
    assert invocation.arguments_dict() == {"a": 1, "z": 2}


def test_capability_shortfall_fails_closed_or_explicitly_downgrades(tmp_path: Path) -> None:
    with pytest.raises(CapabilityShortfall, match="CAPABILITY_SHORTFALL"):
        LedgerRuntime(
            tmp_path,
            deterministic_dependencies(),
            HostCapabilities(),
            requested_profile=EnforcementProfile.STRICT,
        )
    runtime = LedgerRuntime(
        tmp_path,
        deterministic_dependencies(),
        HostCapabilities(),
        requested_profile=EnforcementProfile.STRICT,
        allow_diagnostic_downgrade=True,
    )
    assert runtime.effective_profile is EnforcementProfile.OBSERVE


def test_normalized_runtime_rejects_bad_input_and_denied_approval(tmp_path: Path) -> None:
    runtime = LedgerRuntime(
        tmp_path,
        deterministic_dependencies(),
        _strict_capabilities(),
        requested_profile=EnforcementProfile.STRICT,
    )
    with pytest.raises(RuntimeError, match="has not started"):
        _ = runtime.episode_id
    with pytest.raises(ValueError, match="unsupported"):
        runtime.start_episode(EpisodeContext(2, None, None, None, "x", "x"))
    context = _context()
    first_id = runtime.start_episode(context)
    assert runtime.start_episode(context) == first_id
    with pytest.raises(ValueError, match="green or red"):
        runtime.ingest_health("amber")
    denied = ApprovalResult(
        1,
        context,
        False,
        "",
        "deploy",
        "hash",
        "production",
        "deploy-production",
        "sha256:fixture-policy-v1",
        "exact_action",
    )
    assert runtime.record_approval(denied).reason_code == "APPROVAL_DENIED"
    assert "APPROVAL_RECEIPT_DENIED" in runtime.normalized_events_json()
    assert runtime.ingest_health("red").outcome == "observed"


def test_approval_binding_rejects_mutation_and_accepts_exact_action(tmp_path: Path) -> None:
    runtime = LedgerRuntime(
        tmp_path,
        deterministic_dependencies(),
        _strict_capabilities(),
        requested_profile=EnforcementProfile.STRICT,
    )
    context = _context()
    runtime.start_episode(context)
    runtime.ingest_health("green")
    args = {"environment": "production", "artifact": "app:v1"}
    invocation = ToolInvocation.normalize(context, "deploy", args)
    wrong = ApprovalResult(
        1,
        context,
        True,
        "",
        "deploy",
        "wrong",
        "production",
        "deploy-production",
        "sha256:fixture-policy-v1",
        "exact_action",
    )
    runtime.record_approval(wrong)
    assert runtime.authorize_deployment(invocation).reason_code == "APPROVAL_REQUIRED"
    exact = ApprovalResult(
        1,
        context,
        True,
        "",
        "deploy",
        content_hash(canonical_json(args)),
        "production",
        "deploy-production",
        "sha256:fixture-policy-v1",
        "exact_action",
    )
    runtime.record_approval(exact)
    assert runtime.authorize_deployment(invocation).outcome == "allow"
    runtime.ingest_health("red")
    assert runtime.authorize_deployment(invocation).reason_code == "SUPPORT_RETRACTED"


def test_explicit_core_config_is_host_neutral(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    source.write_text("nested:\n  override: true\n", encoding="utf-8")
    snapshot = load_core_config(
        tmp_path / "state",
        defaults={"nested": {"base": 1, "override": False}},
        explicit_path=source,
    )
    assert snapshot.schema_version == 1
    assert snapshot.data == {"nested": {"base": 1, "override": True}}
    assert len(snapshot.digest) == 64
    empty = load_core_config(tmp_path / "state", defaults={"value": 1})
    assert empty.source is None
    source.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_core_config(tmp_path, defaults={}, explicit_path=source)


def test_deterministic_dependency_fakes_cover_time_identity_token_and_model() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 1, 1))
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    clock.advance(2)
    assert clock.now().second == 2
    monotonic = FixedMonotonicClock()
    monotonic.advance(1.5)
    assert monotonic.now() == 1.5
    identity = SequenceIdentity()
    assert [identity.new("event"), identity.new("event")] == ["event_0001", "event_0002"]
    tokens = SequenceToken(["queued"])
    assert tokens.issue() == "queued"
    assert tokens.issue() == "deterministic-token-0001"
    request = StructuredModelRequest(1, "test", "do", "input", {}, 10, 1.0)
    result = StructuredModelResult(1, {"ok": True}, "fake", "fake")
    fake = FakeStructuredModel([result])
    assert fake.complete(request) is result
    assert fake.requests == [request]
    with pytest.raises(StructuredModelProviderError, match="no deterministic"):
        fake.complete(request)
    callable_port = CallableStructuredModel(lambda value: result)
    assert callable_port.complete(request) is result
