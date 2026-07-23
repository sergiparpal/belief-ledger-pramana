from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

from belief_ledger_core.contracts import EpisodeContext, ToolInvocation
from belief_ledger_core.dependencies import deterministic_dependencies
from belief_ledger_reference.cli import serve_jsonl
from belief_ledger_reference.runner import ReferenceRunner


def _fixture(tmp_path: Path) -> tuple[ReferenceRunner, ToolInvocation]:
    runner = ReferenceRunner(tmp_path, dependencies=deterministic_dependencies())
    context = EpisodeContext.normalize(
        session_id="session-1",
        turn_id="turn-1",
        task_id="task-1",
        platform="test",
        model="fake",
    )
    runner.start(context)
    invocation = ToolInvocation.normalize(
        context,
        "deploy",
        {"artifact": "app:v1", "environment": "production"},
    )
    return runner, invocation


def _permit(runner: ReferenceRunner, invocation: ToolInvocation):
    runner.observe_health("green")
    receipt = runner.approve_deployment(invocation)
    assert receipt is not None
    authorization = runner.authorize(invocation, approval=receipt)
    assert authorization.permit is not None
    return authorization.permit


def test_effectful_handler_is_unreachable_without_successful_consume(tmp_path: Path) -> None:
    runner, invocation = _fixture(tmp_path)
    assert runner.dispatch(invocation).reason_code == "TOKEN_REQUIRED"
    assert runner.deployments == ()

    permit = _permit(runner, invocation)
    changed = ToolInvocation.normalize(
        invocation.context,
        "deploy",
        {"artifact": "app:v1", "environment": "staging"},
    )
    assert runner.dispatch(changed, permit).reason_code == "ARGUMENTS_MISMATCH"
    assert runner.deployments == ()
    assert runner.dispatch(invocation, permit).executed
    assert len(runner.deployments) == 1
    assert runner.dispatch(invocation, permit).reason_code == "TOKEN_CONSUMED"
    assert len(runner.deployments) == 1


def test_cross_turn_retraction_and_handler_crash_fail_closed(tmp_path: Path) -> None:
    runner, invocation = _fixture(tmp_path)
    permit = _permit(runner, invocation)
    other_context = replace(invocation.context, turn_id="turn-2")
    other_turn = replace(invocation, context=other_context)
    assert runner.dispatch(other_turn, permit).reason_code == "TURN_MISMATCH"

    runner.observe_health("red")
    assert runner.dispatch(invocation, permit).reason_code == "TOKEN_REVOKED"

    crashed: list[bool] = []

    def explode(arguments):
        crashed.append(bool(arguments))
        raise RuntimeError("fixture crash")

    runner.register_tool("explode", explode, effectful=True)
    exploding = ToolInvocation.normalize(
        invocation.context,
        "explode",
        {"artifact": "app:v1", "environment": "production"},
    )
    runner.observe_health("green")
    receipt = runner.approve_deployment(exploding)
    assert receipt is not None
    authorized = runner.authorize(exploding, approval=receipt)
    assert authorized.permit is not None
    assert runner.dispatch(exploding, authorized.permit).reason_code == "HANDLER_ERROR"
    assert runner.dispatch(exploding, authorized.permit).reason_code == "TOKEN_CONSUMED"
    assert crashed == [True]


def test_strict_delivery_releases_only_accepted_or_block_report(tmp_path: Path) -> None:
    runner, _ = _fixture(tmp_path)
    allowed = runner.deliver_output(("safe ", "answer"), lint=lambda text: text == "safe answer")
    assert allowed.result.accepted
    assert allowed.deliveries == (b"safe answer",)

    blocked = runner.deliver_output(("provisional secret",), lint=lambda text: False)
    assert not blocked.result.accepted
    assert blocked.deliveries == (b"BLOCKED [OUTPUT_NOT_ACCEPTED]",)
    assert b"provisional secret" not in b"".join(blocked.deliveries)
    buffer_events = [
        event["kind"]
        for event in runner.normalized_events()
        if str(event["kind"]).startswith("OUTPUT_BUFFER_")
    ]
    assert buffer_events == [
        "OUTPUT_BUFFER_STARTED",
        "OUTPUT_BUFFER_ACCEPTED",
        "OUTPUT_BUFFER_STARTED",
        "OUTPUT_BUFFER_DISCARDED",
    ]


def test_jsonl_protocol_dispatches_without_exposing_raw_tokens(tmp_path: Path) -> None:
    requests = (
        '{"schema_version":1,"op":"start","turn_id":"turn-1"}\n'
        '{"schema_version":1,"op":"health","value":"green"}\n'
        '{"schema_version":1,"op":"approve"}\n'
        '{"schema_version":1,"op":"deploy"}\n'
        '{"schema_version":1,"op":"deliver","chunks":["safe"],"accepted":true}\n'
    )
    output = io.StringIO()
    assert serve_jsonl(io.StringIO(requests), output, state_root=tmp_path) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert all(response["ok"] for response in responses)
    assert responses[3]["result"]["executed"] is True
    assert "token" not in output.getvalue().casefold()
