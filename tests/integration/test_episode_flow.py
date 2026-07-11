from __future__ import annotations

from belief_ledger_pramana.models import Pramana, Status


def test_wrapper_content_separation_and_lazy_promotion(runtime) -> None:
    service = runtime.begin_turn(
        session_id="session",
        turn_id="turn-1",
        user_message="Read the package document.",
    )
    original = "Package Foo version is 2.4.1."
    service.ingest_tool_result(
        "read_file",
        {"path": "README.md"},
        original,
        session_id="session",
        turn_id="turn-1",
        tool_call_id="call-1",
        status="success",
    )
    before = service.store.list_beliefs(service.episode_id)
    assert len(before) == 1
    assert before[0].pramana is Pramana.PRATYAKSHA
    assert "read_file" in before[0].content

    service.compile_context(query="Foo version", request_id="request-1")
    after = service.store.list_beliefs(service.episode_id)
    content = [belief for belief in after if belief.content.startswith("Package Foo")]
    assert len(content) == 1
    assert content[0].pramana is Pramana.SHABDA
    assert content[0].source_id != before[0].source_id
    assert service.store.get_evidence(before[0].evidence[0].evidence_id).content_hash


def test_stale_claim_retracts_descendant_and_reinstates_context(runtime) -> None:
    service = runtime.begin_turn(
        session_id="session",
        turn_id="turn-1",
        user_message="Foo version is 2.3.",
        sender_id="user",
    )
    service.ingest_user_message(
        "Foo version is 2.3.",
        session_id="session",
        turn_id="turn-1",
        sender_id="user",
    )
    stale = next(
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.content == "Foo version is 2.3"
    )
    service.compile_context(query="Foo version", request_id="request-before")
    derived, _ = service.record_inference(
        content="Requirements should pin Foo 2.3",
        pramana=Pramana.ANUMANA,
        premise_ids=(stale.id,),
        warrant="The requested pin follows the currently believed latest version",
    )
    service.compile_context(query="pin Foo", request_id="request-derived")

    service.ingest_tool_result(
        "exec_command",
        {"cmd": "pip index versions foo"},
        "Foo version is 2.4.1.",
        session_id="session",
        turn_id="turn-1",
        tool_call_id="call-new",
        status="success",
    )
    rendered = service.compile_context(query="Foo version", request_id="request-after")
    stale_after = service.store.get_belief(stale.id)
    derived_after = service.store.get_belief(derived.id)
    assert stale_after is not None and stale_after.status is Status.OUT
    assert derived_after is not None and derived_after.status is Status.OUT
    fresh = next(
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.content == "Foo version is 2.4.1"
    )
    assert fresh.pramana is Pramana.PRATYAKSHA
    assert fresh.status is Status.IN
    notices = service.store.list_retractions(service.episode_id)
    root_notice = next(item for item in notices if item.defeated_belief_id == stale.id)
    assert derived.id in root_notice.descendants
    assert rendered.text.index("RETRACT") < rendered.text.index("### LEDGER")
    assert stale.id not in rendered.belief_ids
    assert derived.id not in rendered.belief_ids

    replacement = service.lint_and_enforce(f"Foo version is 2.4.1 [{fresh.id}].")
    assert replacement is None
    assert not service.store.list_retractions(service.episode_id)


def test_retrying_tool_hook_is_idempotent(runtime) -> None:
    service = runtime.begin_turn(session_id="session", turn_id="turn", user_message="Inspect.")
    kwargs = {
        "session_id": "session",
        "turn_id": "turn",
        "tool_call_id": "same-call",
        "status": "success",
    }
    first = service.ingest_tool_result("read_file", {"path": "x"}, "X is present.", **kwargs)
    second = service.ingest_tool_result("read_file", {"path": "x"}, "X is present.", **kwargs)
    assert second == first[:1]
    evidence_events = [
        event
        for event in service.store.events(service.episode_id)
        if event.kind == "EVIDENCE_INGESTED"
    ]
    assert len(evidence_events) == 1


def test_negative_search_without_yogyata_is_search_failed(runtime) -> None:
    service = runtime.begin_turn(session_id="session", turn_id="turn", user_message="Search.")
    service.ingest_tool_result(
        "search_files",
        {"query": "legacy_mode"},
        "no results",
        session_id="session",
        turn_id="turn",
        tool_call_id="negative",
        status="success",
    )
    assert not any(
        belief.pramana is Pramana.ANUPALABDHI
        for belief in service.store.list_beliefs(service.episode_id)
    )
    assert any(event.kind == "SEARCH_FAILED" for event in service.store.events(service.episode_id))
