from __future__ import annotations

import io
import json
from pathlib import Path
from typing import cast

import belief_ledger_gateway.cli as gateway_cli
import pytest
from belief_ledger_core import (
    ApprovalResult,
    BeliefLedger,
    EnforcementProfile,
    EpisodeContext,
    HostCapabilities,
    ToolDescriptor,
    ToolInvocation,
)
from belief_ledger_core.events import canonical_json, content_hash
from belief_ledger_gateway.cli import main
from belief_ledger_gateway.dispatcher import GatewayDispatcher
from belief_ledger_gateway.protocol import (
    _READ_CHUNK,
    MAX_IDEMPOTENCY_ENTRIES,
    GatewayService,
    ProtocolError,
    _bounded_lines,
    open_gateway_ledger,
    serve_jsonl,
)


def test_cli_demo_and_private_init(tmp_path: Path, capsys) -> None:
    assert main(["demo", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "observe"
    state = tmp_path / "state"
    assert main(["--state-root", str(state), "init", "--format", "json"]) == 0
    assert state.stat().st_mode & 0o777 == 0o700
    assert (state / "config.yaml").stat().st_mode & 0o777 == 0o600
    assert (state / "policies.json").stat().st_mode & 0o777 == 0o600
    ledger = open_gateway_ledger(state)
    episode = ledger.start_episode(EpisodeContext.normalize(session_id="cli", turn_id="1"))
    for arguments in (
        ["--state-root", str(state), "ledger", "status", "--format", "json"],
        ["--state-root", str(state), "ledger", "replay", "--format", "json"],
        ["--state-root", str(state), "episode", "list", "--format", "json"],
        ["--state-root", str(state), "episode", "show", episode.id, "--format", "json"],
        ["--state-root", str(state), "episode", "export", episode.id, "--format", "json"],
        ["--state-root", str(state), "policy", "inventory", "--format", "json"],
        ["--state-root", str(state), "policy", "scaffold", "mutate", "--format", "json"],
        ["--state-root", str(state), "policy", "explain", "read_item", "--format", "json"],
    ):
        assert main(arguments) == 0
        assert capsys.readouterr().out
    assert main(["--state-root", str(state), "episode", "show", "missing", "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == "EPISODE_NOT_FOUND"


def test_cli_unexpected_errors_do_not_disclose_exception_text(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_args):
        raise RuntimeError("api_key=do-not-disclose")

    monkeypatch.setattr(gateway_cli, "_run", fail)
    assert main(["demo", "--format", "json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["detail"] == "command could not be completed"
    assert "do-not-disclose" not in json.dumps(result)


def test_jsonl_is_bounded_deterministic_idempotent_and_observe_only(tmp_path: Path) -> None:
    start = {
        "schema_version": 1,
        "request_id": "s",
        "idempotency_key": "start",
        "operation": "episode.start",
        "context": {"session_id": "s", "turn_id": "t"},
    }
    requests = "\n".join(
        (
            json.dumps({"schema_version": 1, "request_id": "c", "operation": "capabilities"}),
            json.dumps(start),
            json.dumps(start),
            json.dumps({**start, "request_id": "other"}),
            "{broken",
            json.dumps({"schema_version": 1, "request_id": "x", "operation": "shutdown"}),
        )
    )
    output = io.StringIO()
    assert serve_jsonl(io.StringIO(requests), output, state_root=tmp_path) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["result"]["profile"] == "observe"
    assert responses[1] == responses[2]
    assert responses[3]["error"]["reason_code"] == "IDEMPOTENCY_KEY_REUSED"
    assert responses[4]["error"]["reason_code"] == "MALFORMED_JSON"
    assert "token" not in output.getvalue().casefold()

    oversized = io.StringIO("x" * 20 + "\n")
    bounded = io.StringIO()
    serve_jsonl(oversized, bounded, state_root=tmp_path / "other", max_line_bytes=10)
    assert json.loads(bounded.getvalue())["error"]["reason_code"] == "LINE_TOO_LARGE"


def test_oversized_line_is_rejected_and_the_stream_resynchronizes(tmp_path: Path) -> None:
    valid = json.dumps({"schema_version": 1, "request_id": "c", "operation": "capabilities"})
    source = io.StringIO("x" * 5_000 + "\n" + valid + "\n")
    output = io.StringIO()
    assert serve_jsonl(source, output, state_root=tmp_path, max_line_bytes=256) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses) == 2
    assert responses[0]["error"]["reason_code"] == "LINE_TOO_LARGE"
    assert responses[0]["error"]["line"] == 1
    assert responses[1]["ok"] and responses[1]["request_id"] == "c"
    assert responses[1]["result"]["profile"] == "observe"


def test_oversized_binary_line_is_rejected_and_the_stream_resynchronizes(tmp_path: Path) -> None:
    valid = json.dumps({"schema_version": 1, "request_id": "c", "operation": "capabilities"})
    source = io.BytesIO(b"x" * 5_000 + b"\n" + valid.encode("utf-8") + b"\n")
    output = io.StringIO()
    assert serve_jsonl(source, output, state_root=tmp_path, max_line_bytes=256) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [response.get("error", {}).get("reason_code") for response in responses] == [
        "LINE_TOO_LARGE",
        None,
    ]
    assert responses[1]["result"]["profile"] == "observe"


def test_reader_does_not_buffer_beyond_the_limit() -> None:
    class _RecordingSource:
        """Keeps serving one long line so the reader's own bound is what stops accumulation."""

        def __init__(self, chunk_size: int, chunks: int) -> None:
            self.chunk_size = chunk_size
            self.remaining = chunks
            self.served = 0
            self.largest_request = 0

        def readline(self, size: int = -1) -> str:
            self.largest_request = max(self.largest_request, size)
            if self.remaining <= 0:
                return ""
            self.remaining -= 1
            filler = "x" * (self.chunk_size - 1)
            chunk = filler + ("\n" if not self.remaining else "x")
            self.served += len(chunk)
            return chunk

    max_line_bytes = 1_000
    source = _RecordingSource(chunk_size=100, chunks=40)
    lines = list(_bounded_lines(cast(io.StringIO, source), max_line_bytes))

    # The source served four times the limit and the reader drained all of it to the
    # newline, but never held more than the limit.
    assert source.served == 4_000
    assert source.largest_request == _READ_CHUNK
    assert len(lines) == 1
    payload, oversized = lines[0]
    assert oversized
    assert len(payload) == max_line_bytes


def test_stateful_call_before_start_and_unsupported_operation_fail_closed(tmp_path: Path) -> None:
    service = GatewayService(tmp_path)
    for operation, reason in (
        ("ledger.replay", "EPISODE_NOT_STARTED"),
        ("nope", "UNSUPPORTED_OPERATION"),
    ):
        try:
            service.handle({"schema_version": 1, "operation": operation})
        except ProtocolError as exc:
            assert exc.reason_code == reason
        else:
            raise AssertionError("stateful request unexpectedly succeeded")


def test_jsonl_unexpected_errors_do_not_disclose_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_service, _request):
        raise RuntimeError("password=do-not-disclose")

    monkeypatch.setattr(GatewayService, "handle", fail)
    output = io.StringIO()
    serve_jsonl(
        io.StringIO('{"schema_version":1,"operation":"capabilities"}\n'),
        output,
        state_root=tmp_path,
    )
    response = json.loads(output.getvalue())
    assert response["error"]["reason_code"] == "INVALID_REQUEST"
    assert response["error"]["detail"] == "request could not be processed"
    assert "do-not-disclose" not in output.getvalue()


def test_jsonl_preserves_request_id_on_validation_errors(tmp_path: Path) -> None:
    output = io.StringIO()
    serve_jsonl(
        io.StringIO(
            json.dumps(
                {
                    "schema_version": 1,
                    "request_id": "correlation-42",
                    "operation": "episode.start",
                    "context": {"session_id": {"invalid": True}},
                }
            )
            + "\n"
        ),
        output,
        state_root=tmp_path,
    )
    response = json.loads(output.getvalue())
    assert response["request_id"] == "correlation-42"
    assert response["error"]["reason_code"] == "INVALID_FIELD"


def test_protocol_all_operations_and_validation(tmp_path: Path) -> None:
    service = GatewayService(tmp_path)
    with pytest.raises(ProtocolError, match="schema_version"):
        service.handle({"schema_version": 2, "operation": "capabilities"})
    with pytest.raises(ProtocolError, match="operation"):
        service.handle({"schema_version": 1})
    with pytest.raises(ProtocolError, match="request_id"):
        service.handle({"schema_version": 1, "operation": "capabilities", "request_id": 1})
    with pytest.raises(ProtocolError, match="idempotency_key"):
        service.handle({"schema_version": 1, "operation": "capabilities", "idempotency_key": ""})
    started = service.handle(
        {
            "schema_version": 1,
            "operation": "episode.start",
            "context": {"session_id": "s", "turn_id": "t"},
        }
    )
    episode_id = started["result"]["id"]
    with pytest.raises(ProtocolError) as repeated:
        service.handle({"schema_version": 1, "operation": "episode.start", "context": {}})
    assert repeated.value.reason_code == "EPISODE_ALREADY_STARTED"
    with pytest.raises(ProtocolError) as invalid_name:
        service.handle(
            {
                "schema_version": 1,
                "operation": "action.evaluate",
                "invocation": {"name": None, "arguments": {}},
            }
        )
    assert invalid_name.value.reason_code == "INVALID_FIELD"
    with pytest.raises(ProtocolError) as invalid_boolean:
        service.handle(
            {
                "schema_version": 1,
                "operation": "output.evaluate",
                "content": "Text",
                "final": "false",
            }
        )
    assert invalid_boolean.value.reason_code == "INVALID_FIELD"
    with pytest.raises(ProtocolError) as unknown_observation:
        service.handle(
            {
                "schema_version": 1,
                "operation": "evidence.ingest",
                "observation": {
                    "content": "A current fact",
                    "source_name": "probe",
                    "observed_at": "2026-01-01T00:00:00Z",
                },
            }
        )
    assert unknown_observation.value.reason_code == "INVALID_FIELD"
    evidence = service.handle(
        {
            "schema_version": 1,
            "operation": "evidence.ingest",
            "observation": {"content": "A current fact", "source_name": "probe"},
        }
    )
    assert evidence["result"]["reason_code"] == "EVIDENCE_ADMITTED"
    decision = service.handle(
        {
            "schema_version": 1,
            "operation": "action.evaluate",
            "invocation": {"name": "read_item", "arguments": {}},
        }
    )
    explanation = service.handle(
        {
            "schema_version": 1,
            "operation": "decision.explain",
            "decision_id": decision["result"]["decision_id"],
        }
    )
    assert explanation["result"]["decision"]["outcome"] == "allow"
    output = service.handle(
        {
            "schema_version": 1,
            "operation": "output.evaluate",
            "content": "Unsupported claim.",
            "stakes": "high",
        }
    )
    assert output["result"]["delivered"] is False
    assert service.handle({"schema_version": 1, "operation": "ledger.verify-chain"})["result"][
        "valid"
    ]
    assert service.handle({"schema_version": 1, "operation": "ledger.replay"})["result"][
        "deterministic"
    ]
    assert (
        service.handle({"schema_version": 1, "operation": "episode.finalize"})["result"]["state"]
        == "finalized"
    )
    with pytest.raises(ProtocolError) as unsupported:
        service.handle({"schema_version": 1, "operation": "no-such-operation"})
    assert unsupported.value.reason_code == "UNSUPPORTED_OPERATION"
    assert service.ledger.episode(episode_id).state == "finalized"
    restarted = service.handle(
        {
            "schema_version": 1,
            "operation": "episode.start",
            "context": {"session_id": "s", "turn_id": "next"},
        }
    )
    assert restarted["result"]["id"] != episode_id

    invalid = io.StringIO()
    serve_jsonl(io.BytesIO(b"\xff\n[]\n"), invalid, state_root=tmp_path / "invalid")
    errors = [json.loads(line)["error"]["reason_code"] for line in invalid.getvalue().splitlines()]
    assert errors == ["INVALID_UTF8", "INVALID_ENVELOPE"]


def test_protocol_idempotency_cache_is_bounded(tmp_path: Path) -> None:
    service = GatewayService(tmp_path)
    for index in range(MAX_IDEMPOTENCY_ENTRIES + 20):
        service.handle(
            {
                "schema_version": 1,
                "operation": "capabilities",
                "idempotency_key": f"request-{index}",
            }
        )
    assert len(service._idempotency) == MAX_IDEMPOTENCY_ENTRIES
    assert "request-0" not in service._idempotency


def test_owned_dispatcher_consumes_before_private_handler(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 2,
        "rules": [
            {
                "id": "read",
                "revision": "v1",
                "effectful": False,
                "base_stakes": "low",
                "exact": ["read"],
                "target_fields": [],
                "preconditions": [],
                "approval_policy": "none",
                "minimum_source_integrity": "untrusted",
                "canonicalization_version": 1,
            },
            {
                "id": "send",
                "revision": "v1",
                "effectful": True,
                "base_stakes": "high",
                "exact": ["send"],
                "target_fields": ["recipient"],
                "preconditions": [],
                "approval_policy": "required",
                "minimum_source_integrity": "trusted",
                "canonicalization_version": 1,
            },
        ],
    }
    ledger = BeliefLedger.open(
        state_root=tmp_path,
        manifest=manifest,
        capabilities=HostCapabilities(pre_action_gate=True),
        requested_profile=EnforcementProfile.ACTION_ENFORCE,
    )
    context = EpisodeContext.normalize(session_id="d", turn_id="t")
    episode = ledger.start_episode(context)
    dispatcher = GatewayDispatcher(ledger)
    assert dispatcher.capability_profile == "action_enforce"
    effects: list[str] = []
    dispatcher.register(ToolDescriptor.create("read", {}), lambda args: "read", effectful=False)
    dispatcher.register(
        ToolDescriptor.create(
            "send",
            {
                "type": "object",
                "properties": {"recipient": {"type": "string"}},
                "required": ["recipient"],
                "additionalProperties": False,
            },
        ),
        lambda args: effects.append(args["recipient"]) or "sent",
        effectful=True,
    )
    assert (
        dispatcher.dispatch(episode.id, ToolInvocation.normalize(context, "send", {})).reason_code
        == "INVALID_ARGUMENTS"
    )
    assert effects == []
    assert dispatcher.dispatch(episode.id, ToolInvocation.normalize(context, "read", {})).executed
    invocation = ToolInvocation.normalize(context, "send", {"recipient": "42"})
    assert dispatcher.dispatch(episode.id, invocation).reason_code == "APPROVAL_REQUIRED"
    ledger.record_approval(
        episode.id,
        ApprovalResult(
            1,
            context,
            True,
            "",
            "send",
            content_hash(canonical_json(invocation.arguments_dict())),
            "42",
            "send",
            "v1",
            "exact_action",
        ),
    )
    assert dispatcher.dispatch(episode.id, invocation).executed and effects == ["42"]
    assert (
        dispatcher.dispatch(
            episode.id, ToolInvocation.normalize(context, "missing", {})
        ).reason_code
        == "UNKNOWN_TOOL"
    )
    with pytest.raises(ValueError, match="unique"):
        dispatcher.register(ToolDescriptor.create("read", {}), lambda args: None, effectful=False)
    with pytest.raises(ValueError, match="NO_POLICY"):
        dispatcher.register(ToolDescriptor.create("unknown", {}), lambda args: None, effectful=True)
