from __future__ import annotations

from belief_ledger_pramana.models import Pramana, VerificationMethod


def _derived(runtime):
    service = runtime.begin_turn(
        session_id="s",
        turn_id="t",
        user_message="The package is stable.",
        sender_id="u",
    )
    service.ingest_user_message(
        "The package is stable.", session_id="s", turn_id="t", sender_id="u"
    )
    premise = next(
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.content == "The package is stable"
    )
    derived, _ = service.record_inference(
        content="The deployment can use the package",
        pramana=Pramana.ANUMANA,
        premise_ids=(premise.id,),
        warrant="Stable packages can be selected for this deployment",
    )
    task, _ = service.request_verification(derived.id, VerificationMethod.CHAIN_AUDIT)
    return service, derived, task


def test_chain_audit_is_persisted_and_component_is_auditable(runtime, fake_ctx) -> None:
    service, derived, task = _derived(runtime)
    fake_ctx.llm.queue(
        {
            "paksadharmata": True,
            "sapakse_sattvam": True,
            "vipakse_asattvam": True,
            "evidence_ids": [],
            "fallacies": [],
            "basis": "all checks passed",
        }
    )
    event_ids = service.run_chain_audit(task)
    assert event_ids
    refreshed = service.store.get_belief(derived.id)
    assert refreshed is not None
    assert refreshed.justifications[0].audit is not None
    assert (
        service.store.list_verification_tasks(service.episode_id, state="completed")[0].result
        == "confirmed"
    )
    verdicts = [
        event
        for event in service.store.events(service.episode_id)
        if event.kind == "COMPONENT_VERDICT_RECORDED"
        and event.payload["record"]["component"] == "chain_auditor"
    ]
    assert verdicts
    assert verdicts[-1].payload["record"]["belief_id"].startswith("b_")


def test_malformed_chain_audit_remains_open_and_bounded(runtime, fake_ctx) -> None:
    service, _, task = _derived(runtime)
    fake_ctx.llm.queue({"bad": "schema"})
    assert service.run_chain_audit(task) == ()
    open_tasks = service.store.list_verification_tasks(service.episode_id, state="open")
    assert any(item.id == task.id for item in open_tasks)
    usage = [
        event
        for event in service.store.events(service.episode_id)
        if event.kind == "LLM_USAGE_RECORDED"
    ]
    assert len(usage) == 1
