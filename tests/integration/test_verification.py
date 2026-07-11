from __future__ import annotations

from belief_ledger_pramana.models import Pramana, Status


def test_independent_web_roots_complete_cross_source_verification(runtime) -> None:
    service = runtime.begin_turn(session_id="s", turn_id="t", user_message="Check release")
    service.ingest_tool_result(
        "web_fetch",
        {"url": "https://one.example/release"},
        "Package Foo is stable.",
        session_id="s",
        turn_id="t",
        tool_call_id="one",
        status="success",
    )
    service.ingest_tool_result(
        "web_fetch",
        {"url": "https://independent.test/release"},
        "Package Foo is stable.",
        session_id="s",
        turn_id="t",
        tool_call_id="two",
        status="success",
    )
    service.compile_context(query="Package Foo stable", request_id="r")
    claims = [
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.pramana is Pramana.SHABDA and belief.content == "Package Foo is stable"
    ]
    assert len(claims) == 2
    assert all(belief.status is Status.IN for belief in claims)
    completed = service.store.list_verification_tasks(service.episode_id, state="completed")
    assert len(completed) == 2
    assert all(task.result == "confirmed" for task in completed)


def test_same_domain_mirrors_do_not_corroborate(runtime) -> None:
    service = runtime.begin_turn(session_id="s", turn_id="t", user_message="Check release")
    for index, url in enumerate(("https://mirror.example/a", "https://mirror.example/b"), start=1):
        service.ingest_tool_result(
            "web_fetch",
            {"url": url},
            "Package Foo is stable.",
            session_id="s",
            turn_id="t",
            tool_call_id=f"mirror-{index}",
            status="success",
        )
    service.compile_context(query="Package Foo stable", request_id="r")
    claims = [
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.pramana is Pramana.SHABDA and belief.content == "Package Foo is stable"
    ]
    assert len(claims) == 1
    assert claims[0].status is Status.PENDING
