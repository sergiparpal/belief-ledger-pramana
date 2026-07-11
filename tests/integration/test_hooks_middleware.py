from __future__ import annotations

from belief_ledger_pramana.hermes.hooks import HermesHooks
from belief_ledger_pramana.hermes.middleware import LlmRequestMiddleware


def test_tool_result_hook_never_changes_result(runtime) -> None:
    hooks = HermesHooks(runtime)
    result = '{"value":"unchanged"}'
    returned = hooks.transform_tool_result(
        tool_name="read_file",
        arguments={"path": "x"},
        result=result,
        session_id="s",
        turn_id="t",
        tool_call_id="tc",
        status="success",
    )
    assert returned is None


def test_per_request_middleware_injects_without_mutating_original(runtime) -> None:
    hooks = HermesHooks(runtime)
    hooks.pre_llm_call(
        session_id="s",
        task_id="",
        turn_id="t",
        user_message="The package is stable.",
        sender_id="user",
        platform="cli",
    )
    request = {"messages": [{"role": "user", "content": "Question"}], "temperature": 0.2}
    output = LlmRequestMiddleware(runtime)(
        request=request,
        session_id="s",
        turn_id="t",
        api_request_id="r",
        api_mode="chat_completions",
    )
    assert output is not None
    assert request == {"messages": [{"role": "user", "content": "Question"}], "temperature": 0.2}
    assert "BELIEF_LEDGER_PRAMANA:BEGIN" in output["request"]["messages"][-1]["content"]
    assert output["request"]["temperature"] == 0.2
    assert (
        LlmRequestMiddleware(runtime)(
            request=output["request"],
            session_id="s",
            turn_id="t",
            api_request_id="r",
            api_mode="chat_completions",
        )
        is None
    )


def test_unknown_provider_shape_records_degradation(runtime) -> None:
    runtime.begin_turn(session_id="s", turn_id="t", user_message="Question")
    output = LlmRequestMiddleware(runtime)(
        request={"future": []},
        session_id="s",
        turn_id="t",
        api_request_id="r",
        api_mode="future_mode",
    )
    assert output is None
    service = runtime.service(session_id="s")
    assert runtime.injection_failed(service.episode_id)
    assert any(
        event.kind == "CONTEXT_INJECTION_FAILED"
        for event in service.store.events(service.episode_id)
    )
