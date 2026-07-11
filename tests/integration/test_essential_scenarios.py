from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from belief_ledger_pramana.hermes.hooks import HermesHooks
from belief_ledger_pramana.models import Pramana, SourceKind, Stakes, Status


def test_web_fetch_separates_observation_from_testimony(runtime) -> None:
    service = runtime.begin_turn(
        session_id="web-session",
        turn_id="web-turn",
        user_message="What version is Aurora?",
    )
    service.ingest_tool_result(
        "fetch_url",
        {"url": "https://docs.example.test/aurora"},
        "Product Aurora version is 7.",
        session_id="web-session",
        turn_id="web-turn",
        tool_call_id="web-call",
        status="success",
    )
    service.compile_context(query="Aurora version", request_id="web-context")
    beliefs = service.store.list_beliefs(service.episode_id)
    wrapper = next(item for item in beliefs if "fetch_url return success" in item.content)
    content = next(item for item in beliefs if item.content == "Product Aurora version is 7")
    wrapper_source = service.store.get_source(wrapper.source_id)
    content_source = service.store.get_source(content.source_id)
    assert wrapper.pramana is Pramana.PRATYAKSHA
    assert wrapper_source is not None and wrapper_source.kind is SourceKind.TOOL
    assert content.pramana is Pramana.SHABDA
    assert content_source is not None and content_source.kind is SourceKind.WEB
    assert content.status is Status.PENDING


def test_live_memory_transport_reentry_requires_reobservation(runtime, fake_ctx) -> None:
    text = "Service pulse is healthy."
    fake_ctx.llm.queue(
        {
            "claims": [
                {
                    "content": "Service pulse is healthy",
                    "pramana": "shabda",
                    "span_start": 0,
                    "span_end": len(text),
                    "exact_excerpt": text,
                    "qualifiers": {},
                    "domain": "runtime_state",
                    "perishability": "live",
                    "speech_act": "asserting",
                    "source_identity": "prior ledger",
                }
            ]
        }
    )
    service = runtime.begin_turn(
        session_id="memory-session",
        turn_id="memory-turn",
        user_message="Is the service healthy now?",
    )
    service.ingest_tool_result(
        "memory_retrieve",
        {"memory_id": "prior-episode", "source_root": "tool:health-probe"},
        text,
        session_id="memory-session",
        turn_id="memory-turn",
        tool_call_id="memory-call",
        status="success",
    )
    service.compile_context(query="service pulse healthy", request_id="memory-context")
    belief = next(
        item
        for item in service.store.list_beliefs(service.episode_id)
        if item.content == "Service pulse is healthy"
    )
    source = service.store.get_source(belief.source_id)
    assert source is not None and source.kind is SourceKind.LEDGER
    assert belief.status is Status.PENDING
    tasks = service.store.list_verification_tasks(service.episode_id, state="open")
    assert any(
        task.belief_id == belief.id and task.method.value == "tool_recheck" for task in tasks
    )


def test_extractor_and_linter_verdicts_are_auditable_anumana(runtime) -> None:
    service = runtime.begin_turn(
        session_id="audit-session",
        turn_id="audit-turn",
        user_message="Inspect Atlas.",
    )
    service.ingest_tool_result(
        "read_file",
        {"path": "ATLAS.md"},
        "Atlas mode is deterministic.",
        session_id="audit-session",
        turn_id="audit-turn",
        tool_call_id="audit-read",
        status="success",
    )
    service.compile_context(query="Atlas mode", request_id="audit-context")
    atlas = next(
        item
        for item in service.store.list_beliefs(service.episode_id)
        if item.content == "Atlas mode is deterministic"
    )
    service.lint_and_enforce(f"Atlas mode is deterministic [{atlas.id}].")
    component_events = [
        event
        for event in service.store.events(service.episode_id)
        if event.kind == "COMPONENT_VERDICT_RECORDED"
    ]
    by_component: dict[str, list[str]] = {}
    for event in component_events:
        record = event.payload["record"]
        belief_id = record.get("belief_id")
        if belief_id:
            by_component.setdefault(str(record["component"]), []).append(str(belief_id))
    assert by_component["claim_extractor"]
    assert by_component["output_linter"]
    for belief_id in (*by_component["claim_extractor"], *by_component["output_linter"]):
        verdict_belief = service.store.get_belief(belief_id)
        assert verdict_belief is not None
        assert verdict_belief.pramana is Pramana.ANUMANA
        assert verdict_belief.justifications


def test_parallel_independent_tool_batches_have_order_invariant_semantics(runtime) -> None:
    calls = (
        ("parallel-a", {"path": "A.md"}, "Component A is enabled."),
        ("parallel-b", {"path": "B.md"}, "Component B is disabled."),
    )

    def populate(session: str, *, concurrent: bool) -> tuple[tuple[str, str, str], ...]:
        service = runtime.begin_turn(
            session_id=session,
            turn_id=f"{session}-turn",
            user_message="Inspect both components.",
        )

        def ingest(item: tuple[str, dict[str, str], str]) -> None:
            call_id, args, result = item
            service.ingest_tool_result(
                "read_file",
                args,
                result,
                session_id=session,
                turn_id=f"{session}-turn",
                tool_call_id=call_id,
                status="success",
            )

        if concurrent:
            with ThreadPoolExecutor(max_workers=2) as executor:
                tuple(executor.map(ingest, calls))
        else:
            for item in reversed(calls):
                ingest(item)
        service.compile_context(query="Component enabled disabled", request_id=f"{session}-ctx")
        evidence_events = [
            event
            for event in service.store.events(service.episode_id)
            if event.kind == "EVIDENCE_INGESTED"
            and event.correlation.get("tool_call_id") in {"parallel-a", "parallel-b"}
        ]
        assert len(evidence_events) == 2
        return tuple(
            sorted(
                (belief.content, belief.pramana.value, belief.status.value)
                for belief in service.store.list_beliefs(service.episode_id)
                if belief.domain != "monitoring"
            )
        )

    assert populate("parallel-one", concurrent=True) == populate("parallel-two", concurrent=False)


def test_high_effectful_action_never_reaches_handler_and_denial_is_recorded(runtime) -> None:
    hooks = HermesHooks(runtime)
    service = runtime.begin_turn(
        session_id="gate-session",
        turn_id="gate-turn",
        user_message="Inspect before changing anything.",
    )
    service.set_stakes(Stakes.HIGH, user_initiated=True)
    outcome = hooks.pre_tool_call(
        session_id="gate-session",
        turn_id="gate-turn",
        tool_name="write_file",
        args={"path": "/tmp/target", "content": "unsafe"},
    )
    handler_calls = 0
    if outcome is None:
        handler_calls += 1
    assert outcome is not None and outcome["action"] == "block"
    assert handler_calls == 0

    hooks.post_approval_response(
        session_id="gate-session",
        session_key="approval-key",
        command="delete /tmp/target",
        pattern_key="filesystem-delete",
        choice="deny",
        surface="cli",
    )
    approval = next(
        event
        for event in reversed(service.store.events(service.episode_id))
        if event.kind == "APPROVAL_RESPONSE_RECORDED"
    )
    assert approval.payload["confirmed"] is False
