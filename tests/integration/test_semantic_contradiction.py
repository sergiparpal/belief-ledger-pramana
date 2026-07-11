from __future__ import annotations

from belief_ledger_pramana.models import Status


def test_structured_contradiction_verdict_creates_rebut_and_r5_belief(runtime, fake_ctx) -> None:
    service = runtime.begin_turn(
        session_id="s",
        turn_id="t1",
        user_message="Service Atlas is healthy.",
        sender_id="u",
    )
    service.ingest_user_message(
        "Service Atlas is healthy.", session_id="s", turn_id="t1", sender_id="u"
    )
    fake_ctx.llm.queue(
        {
            "outcome": "rebut",
            "left_scope": {},
            "right_scope": {},
            "basis": "healthy and down are incompatible runtime states",
        }
    )
    runtime.begin_turn(
        session_id="s",
        turn_id="t2",
        user_message="Service Atlas is down.",
        sender_id="u",
    )
    service = runtime.service(session_id="s")
    service.ingest_user_message(
        "Service Atlas is down.", session_id="s", turn_id="t2", sender_id="u"
    )
    claims = [
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.content in {"Service Atlas is healthy", "Service Atlas is down"}
    ]
    assert len(claims) == 2
    assert all(belief.status is Status.PENDING for belief in claims)
    assert len(service.store.list_defeats(service.episode_id)) == 2
    verdicts = [
        event.payload["record"]
        for event in service.store.events(service.episode_id)
        if event.kind == "COMPONENT_VERDICT_RECORDED"
        and event.payload["record"]["component"] == "contradiction_classifier"
    ]
    assert verdicts and verdicts[-1]["belief_id"].startswith("b_")
