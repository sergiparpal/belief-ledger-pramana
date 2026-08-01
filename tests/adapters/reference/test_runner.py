from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from belief_ledger_core import (
    ApprovalResult,
    EpisodeContext,
    EvidenceObservation,
    ToolDescriptor,
    ToolInvocation,
    deterministic_dependencies,
)
from belief_ledger_core.events import canonical_json, content_hash
from belief_ledger_reference.cli import serve_jsonl
from belief_ledger_reference.runner import ReferenceRunner


def _fixture(tmp_path: Path, handler=None) -> tuple[ReferenceRunner, ToolInvocation, list[dict]]:
    effects: list[dict] = []
    runner = ReferenceRunner(tmp_path, dependencies=deterministic_dependencies())
    runner.register_tool(
        ToolDescriptor.create(
            "send_customer_message",
            {"type": "object", "properties": {"recipient": {"type": "string"}}},
            namespace="crm",
        ),
        handler or (lambda arguments: effects.append(dict(arguments)) or {"sent": True}),
        effectful=True,
        policy={
            "id": "customer-message",
            "revision": "policy-v1",
            "effectful": True,
            "base_stakes": "high",
            "target_fields": ["recipient"],
            "preconditions": ["recipient_identity"],
            "approval_policy": "required",
            "minimum_source_integrity": "trusted",
            "canonicalization_version": 1,
        },
    )
    context = EpisodeContext.normalize(
        session_id="session-1", turn_id="turn-1", task_id="task-1", platform="test"
    )
    runner.start(context)
    invocation = ToolInvocation.normalize(
        context,
        "send_customer_message",
        {"recipient": "customer-42", "body": "hello"},
        namespace="crm",
    )
    return runner, invocation, effects


def _permit(runner: ReferenceRunner, invocation: ToolInvocation):
    evidence = runner.ingest_evidence(
        EvidenceObservation.normalize(
            "Precondition recipient_identity holds for customer-42",
            source_name="directory",
            source_integrity="trusted",
        )
    )
    runner.record_approval(
        ApprovalResult(
            1,
            invocation.context,
            True,
            invocation.namespace,
            invocation.name,
            content_hash(canonical_json(invocation.arguments_dict())),
            "customer-42",
            "customer-message",
            "policy-v1",
            "exact_action",
        )
    )
    authorization = runner.authorize(invocation)
    assert authorization.permit is not None
    return authorization.permit, evidence


def test_runner_starts_empty_and_requires_explicit_policy(tmp_path: Path) -> None:
    runner = ReferenceRunner(tmp_path)
    assert runner.tool_inventory() == ()
    try:
        runner.register_tool("unknown", lambda arguments: arguments, effectful=True)
    except ValueError as exc:
        assert "explicit matching policy" in str(exc)
    else:
        raise AssertionError("registration without policy unexpectedly succeeded")


def test_effectful_handler_is_unreachable_without_successful_consume(tmp_path: Path) -> None:
    runner, invocation, effects = _fixture(tmp_path)
    assert runner.dispatch(invocation).reason_code == "TOKEN_REQUIRED"
    permit, _ = _permit(runner, invocation)
    changed = ToolInvocation.normalize(
        invocation.context,
        invocation.name,
        {"recipient": "customer-7", "body": "hello"},
        namespace="crm",
    )
    assert runner.dispatch(changed, permit).reason_code == "ARGUMENTS_MISMATCH"
    assert effects == []
    assert runner.dispatch(invocation, permit).executed
    assert len(effects) == 1
    assert runner.dispatch(invocation, permit).reason_code == "TOKEN_CONSUMED"
    assert len(effects) == 1


def test_concurrent_dispatch_consumes_once_and_executes_once(tmp_path: Path) -> None:
    runner, invocation, effects = _fixture(tmp_path)
    permit, _ = _permit(runner, invocation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: runner.dispatch(invocation, permit), range(2)))
    assert sum(result.executed for result in results) == 1
    assert sorted(result.reason_code for result in results) == ["DISPATCHED", "TOKEN_CONSUMED"]
    assert len(effects) == 1


def test_retraction_and_handler_crash_fail_closed(tmp_path: Path) -> None:
    calls: list[bool] = []

    def explode(arguments):
        calls.append(bool(arguments))
        raise RuntimeError("crash")

    runner, invocation, _ = _fixture(tmp_path, explode)
    permit, evidence = _permit(runner, invocation)
    runner.retract_support(evidence.belief_id)
    assert runner.dispatch(invocation, permit).reason_code == "TOKEN_REVOKED"

    other, other_invocation, _ = _fixture(tmp_path / "other", explode)
    crash_permit, _ = _permit(other, other_invocation)
    assert other.dispatch(other_invocation, crash_permit).reason_code == "HANDLER_ERROR"
    assert other.dispatch(other_invocation, crash_permit).reason_code == "TOKEN_CONSUMED"
    assert calls == [True]


def test_strict_delivery_releases_only_accepted_or_block_report(tmp_path: Path) -> None:
    runner, _, _ = _fixture(tmp_path)
    allowed = runner.deliver_output(("safe ", "answer"), lint=lambda text: text == "safe answer")
    assert allowed.deliveries == (b"safe answer",)
    blocked = runner.deliver_output(("provisional secret",), lint=lambda text: False)
    assert blocked.deliveries == (b"BLOCKED [OUTPUT_NOT_ACCEPTED]",)
    assert b"provisional secret" not in b"".join(blocked.deliveries)


def test_reference_jsonl_delegates_to_observe_gateway_without_tokens(tmp_path: Path) -> None:
    requests = (
        '{"schema_version":1,"request_id":"c","operation":"capabilities"}\n'
        '{"schema_version":1,"request_id":"s","operation":"episode.start","context":{"turn_id":"t"}}\n'
        '{"schema_version":1,"request_id":"x","operation":"shutdown"}\n'
    )
    output = io.StringIO()
    assert serve_jsonl(io.StringIO(requests), output, state_root=tmp_path) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["result"]["profile"] == "observe"
    assert all(response["ok"] for response in responses)
    assert "token" not in output.getvalue().casefold()
