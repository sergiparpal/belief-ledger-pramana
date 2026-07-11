from __future__ import annotations

import argparse
import json
from dataclasses import replace

from belief_ledger_pramana.hermes.cli import doctor, run_cli, setup_cli
from belief_ledger_pramana.hermes.hooks import HermesHooks
from belief_ledger_pramana.hermes.slash_commands import build_ledger_command
from belief_ledger_pramana.hermes.tools import build_tool_handlers
from belief_ledger_pramana.models import CompatibilityMode, Health, Pramana, Stakes


def _arguments(*parts: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    setup_cli(parser)
    return parser.parse_args(list(parts))


def test_operator_cli_and_slash_command_cover_normal_workflow(runtime) -> None:
    service = runtime.begin_turn(
        session_id="operator-session",
        turn_id="operator-turn",
        user_message="System Atlas is operational.",
        sender_id="operator",
    )
    service.ingest_user_message(
        "System Atlas is operational.",
        session_id="operator-session",
        turn_id="operator-turn",
        sender_id="operator",
    )
    belief = next(
        item
        for item in service.store.list_beliefs(service.episode_id)
        if item.content == "System Atlas is operational"
    )

    for command in (
        ("config", "path"),
        ("config", "show"),
        ("config", "validate"),
        ("config", "init"),
        ("db", "status"),
        ("db", "migrate"),
        ("db", "verify-chain"),
        ("db", "replay"),
        ("episode", "list"),
        ("episode", "show", service.episode_id),
        ("episode", "export", service.episode_id, "--format", "jsonl"),
        ("episode", "export", service.episode_id, "--format", "markdown"),
        ("evaluate", "--suite", "a", "--offline"),
    ):
        code, output = run_cli(runtime, _arguments(*command))
        assert code == 0, output
        assert output

    code, missing = run_cli(runtime, _arguments("episode", "show", "ep_missing"))
    assert code == 2
    assert json.loads(missing)["error"] == "episode_not_found"
    code, mismatch = run_cli(
        runtime,
        _arguments("purge", "--episode", service.episode_id, "--confirm", "wrong-episode"),
    )
    assert code == 2
    assert json.loads(mismatch)["error"] == "confirmation_mismatch"

    ledger = build_ledger_command(runtime)
    assert json.loads(ledger("status"))["episode_id"] == service.episode_id
    assert ledger("help").startswith("/ledger status")
    assert json.loads(ledger("conflicts")) == []
    assert json.loads(ledger("retractions")) == []
    assert json.loads(ledger(f"belief {belief.id}"))["belief"]["id"] == belief.id
    assert json.loads(ledger("stakes high"))["stakes"] == "high"
    assert "written to" in ledger("export jsonl")
    assert "export format" in ledger("export xml")
    assert "unknown or incomplete" in ledger("not-a-command")
    assert "invalid arguments" in ledger("'unterminated")


def test_doctor_reports_activation_and_transform_competition(runtime) -> None:
    assert runtime.paths is not None
    runtime.paths.hermes_home.joinpath("config.yaml").write_text(
        "plugins:\n  enabled: [belief-ledger-pramana]\n  disabled: []\n",
        encoding="utf-8",
    )
    required_tools = {
        "pramana_record_inference",
        "pramana_query",
        "pramana_explain",
        "pramana_request_verification",
    }
    runtime.ctx._manager._plugin_tool_names.update(required_tools)

    def own(**kwargs):
        return kwargs.get("response_text")

    runtime.transform_callback = own
    runtime.ctx._manager._hooks["transform_llm_output"] = [own]
    report = doctor(runtime)
    assert report["status"] == "healthy"
    assert report["full_conformance"] is True
    assert report["checks"]["activation"]["explicitly_enabled"] is True

    def competitor(**kwargs):
        return kwargs.get("response_text")

    runtime.ctx._manager._hooks["transform_llm_output"] = [competitor, own]
    competed = doctor(runtime)
    assert competed["status"] == "unavailable"
    assert competed["checks"]["competing_transformers"]
    assert "transform lacks effective precedence" in " ".join(competed["errors"])


def test_model_tools_succeed_and_every_error_is_json(runtime) -> None:
    service = runtime.begin_turn(
        session_id="tool-session",
        turn_id="tool-turn",
        user_message="The deployment target is staging.",
        sender_id="user",
    )
    service.ingest_user_message(
        "The deployment target is staging.",
        session_id="tool-session",
        turn_id="tool-turn",
        sender_id="user",
    )
    premise = next(
        item
        for item in service.store.list_beliefs(service.episode_id)
        if item.content == "The deployment target is staging"
    )
    handlers = build_tool_handlers(runtime)
    host = {"session_id": "tool-session", "turn_id": "tool-turn", "future": True}

    inference = json.loads(
        handlers["pramana_record_inference"](
            {
                "content": "Deployment should target staging",
                "kind": "anumana",
                "premise_ids": [premise.id],
                "warrant": "The chosen target follows the recorded deployment target",
                "qualifiers": {"scope": "deployment"},
                "perishability": "fast",
            },
            **host,
        )
    )
    assert inference["ok"] is True
    derived_id = inference["data"]["belief_id"]

    query = json.loads(
        handlers["pramana_query"](
            {
                "query": "deployment staging",
                "statuses": ["in"],
                "types": ["anumana", "shabda"],
                "limit": 10,
                "expand_graph": True,
            },
            **host,
        )
    )
    assert query["ok"] is True and query["data"]["count"] >= 2
    explanation = json.loads(
        handlers["pramana_explain"]({"belief_id": derived_id, "depth": 3}, **host)
    )
    assert explanation["ok"] is True
    assert explanation["data"]["belief"]["pramana"] == Pramana.ANUMANA.value
    verification = json.loads(
        handlers["pramana_request_verification"](
            {"belief_id": derived_id, "method": "chain_audit"}, **host
        )
    )
    assert verification["ok"] is True
    assert verification["data"]["scheduled_is_confirmation"] is False

    invalid_payloads = (
        ("pramana_query", {"query": "x", "limit": True}),
        ("pramana_explain", {"belief_id": "missing", "extra": "no"}),
        (
            "pramana_record_inference",
            {
                "content": "x",
                "kind": "anumana",
                "premise_ids": [premise.id, premise.id],
                "warrant": "x",
                "perishability": "slow",
            },
        ),
        ("pramana_request_verification", {"belief_id": derived_id, "method": "unbounded"}),
    )
    for name, payload in invalid_payloads:
        result = json.loads(handlers[name](payload, **host))
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_arguments"


def test_lifecycle_hooks_are_episode_scoped_and_exception_safe(runtime, monkeypatch) -> None:
    hooks = HermesHooks(runtime)
    base = {"session_id": "lifecycle", "turn_id": "life-turn"}
    hooks.on_session_start(**base)
    service = runtime.service(**base)
    hooks.post_llm_call(**base, assistant_response="A response without secrets.")
    hooks.subagent_start(
        parent_session_id="lifecycle",
        parent_turn_id="life-turn",
        child_session_id="child-1",
        child_role="researcher",
        child_goal="Inspect evidence",
    )
    hooks.subagent_stop(
        parent_session_id="lifecycle",
        parent_turn_id="life-turn",
        child_session_id="child-1",
        child_role="researcher",
        child_summary="The delegated result is complete.",
        child_status="completed",
        duration_ms=12,
    )
    hooks.post_approval_response(
        session_id="lifecycle",
        session_key="life-approval",
        command="publish report",
        pattern_key="publish",
        choice="timeout",
        surface="gateway",
    )
    hooks.on_session_end(**base)
    kinds = {event.kind for event in service.store.events(service.episode_id)}
    assert {
        "SESSION_STARTED",
        "ASSISTANT_RESPONSE_RECORDED",
        "SUBAGENT_STARTED",
        "APPROVAL_RESPONSE_RECORDED",
    } <= kinds
    assert runtime.resolve_episode_id(session_key="life-approval") == service.episode_id

    assert hooks.pre_verify(
        **base, final_response="The lunar base is operational.", attempt=0, coding=True
    )
    assert (
        hooks.pre_verify(
            **base, final_response="The lunar base is operational.", attempt=1, coding=True
        )
        is None
    )

    runtime.health = Health.DEGRADED
    service.set_stakes(Stakes.HIGH, user_initiated=True)
    blocked = hooks.transform_llm_output(**base, response_text="Unsupported claim.")
    assert blocked is not None and blocked.startswith("Response blocked")

    original_service = runtime.service
    monkeypatch.setattr(runtime, "service", lambda **kwargs: (_ for _ in ()).throw(RuntimeError()))
    assert hooks.pre_tool_call(tool_name="read_file", args={"path": "x"}, **base) is None
    failed_gate = hooks.pre_tool_call(tool_name="write_file", args={"path": "x"}, **base)
    assert failed_gate is not None and failed_gate["action"] == "block"
    assert hooks.transform_tool_result(tool_name="read_file", result="x", **base) is None
    hooks.post_llm_call(**base, assistant_response="ignored")
    assert hooks.pre_verify(**base, final_response="ignored", attempt=0, coding=True) is None
    monkeypatch.setattr(runtime, "service", original_service)

    hooks.on_session_finalize(**base)
    assert service.store.get_episode(service.episode_id).state == "finalized"

    reset_service = runtime.service(session_id="reset-lifecycle", turn_id="reset-turn")
    hooks.on_session_reset(
        session_id="reset-lifecycle", turn_id="reset-turn", reason="test reset boundary"
    )
    reset_episode = reset_service.store.get_episode(reset_service.episode_id)
    assert reset_episode is not None and reset_episode.state == "reset"
    assert any(
        event.kind == "SESSION_RESET_STARTED"
        for event in reset_service.store.events(reset_service.episode_id)
    )
    replacement = runtime.service(session_id="reset-lifecycle", turn_id="new-reset-turn")
    assert replacement.episode_id != reset_service.episode_id


def test_compatibility_fallback_hooks_are_visibly_degraded(runtime, monkeypatch) -> None:
    hooks = HermesHooks(runtime)
    hook_context = replace(runtime.compatibility, mode=CompatibilityMode.HOOK_CONTEXT)
    runtime.compatibility = hook_context
    injected = hooks.pre_llm_call(
        session_id="compat-session",
        turn_id="compat-turn",
        user_message="Compatibility mode is active.",
        sender_id="user",
    )
    assert injected is not None and "BELIEF_LEDGER_PRAMANA:BEGIN" in injected["context"]

    runtime.compatibility = replace(
        runtime.compatibility,
        mode=CompatibilityMode.DIAGNOSTICS_ONLY,
        errors=("unsupported contract",),
    )
    diagnostic = hooks.pre_llm_call(
        session_id="diag-session",
        turn_id="diag-turn",
        user_message="Diagnostic request.",
    )
    assert diagnostic is not None and "diagnostics-only" in diagnostic["context"]
    assert (
        hooks.pre_tool_call(
            session_id="diag-session",
            turn_id="diag-turn",
            tool_name="read_file",
            args={"path": "x"},
        )
        is None
    )
    blocked = hooks.pre_tool_call(
        session_id="diag-session",
        turn_id="diag-turn",
        tool_name="write_file",
        args={"path": "x"},
    )
    assert blocked is not None and blocked["action"] == "block"
    transformed = hooks.transform_llm_output(
        session_id="diag-session",
        turn_id="diag-turn",
        response_text="Candidate response.",
    )
    assert transformed is not None and "diagnostics-only" in transformed

    monkeypatch.setattr(
        runtime, "begin_turn", lambda **kwargs: (_ for _ in ()).throw(RuntimeError())
    )
    degraded = hooks.pre_llm_call(user_message="Failure path")
    assert degraded is not None and "degraded" in degraded["context"]
