from __future__ import annotations

from belief_ledger_pramana.lint.report import lint_response
from belief_ledger_pramana.models import GateOutcome, Stakes


def test_linter_accepts_valid_citation_and_marks_unsupported(runtime) -> None:
    service = runtime.begin_turn(
        session_id="s",
        turn_id="t",
        user_message="The sky is blue.",
        sender_id="u",
    )
    service.ingest_user_message("The sky is blue.", session_id="s", turn_id="t", sender_id="u")
    belief = service.store.list_beliefs(service.episode_id)[0]
    marker = service.config["lint"]["pending_marker"]
    grounded = lint_response(
        f"The sky is blue [{belief.id}].",
        [belief],
        pending_marker=marker,
    )
    assert grounded.passed
    unsupported = lint_response("The moon is green.", [belief], pending_marker=marker)
    assert not unsupported.passed


def test_med_rewrite_is_bounded_and_falls_back_to_speculation(runtime, fake_ctx) -> None:
    service = runtime.begin_turn(session_id="s", turn_id="t", user_message="Question")
    replacement = service.lint_and_enforce("The moon is green.")
    assert replacement is not None
    assert replacement.startswith("speculation:")
    assert len(fake_ctx.llm.calls) == 1


def test_bounded_semantic_lint_only_assesses_candidate_entailment(runtime, fake_ctx) -> None:
    service = runtime.begin_turn(
        session_id="s",
        turn_id="t",
        user_message="Service Atlas is operational.",
        sender_id="u",
    )
    service.ingest_user_message(
        "Service Atlas is operational.", session_id="s", turn_id="t", sender_id="u"
    )
    belief = next(
        item
        for item in service.store.list_beliefs(service.episode_id)
        if item.content == "Service Atlas is operational"
    )
    fake_ctx.llm.queue(
        {
            "pairs": [
                {
                    "claim_index": 0,
                    "belief_id": belief.id,
                    "entailed": True,
                    "basis": "operational entails healthy in the supplied wording",
                }
            ]
        }
    )
    assert service.lint_and_enforce(f"Service Atlas is healthy [{belief.id}].") is None
    assert fake_ctx.llm.calls[0]["purpose"] == "belief-ledger.lint-entailment"


def test_high_unsupported_output_is_blocked(runtime) -> None:
    service = runtime.begin_turn(session_id="s", turn_id="t", user_message="Question")
    service.set_stakes(Stakes.HIGH, user_initiated=True)
    replacement = service.lint_and_enforce("The moon is green.")
    assert replacement is not None
    assert replacement.startswith("Response blocked")


def test_action_gate_allows_reads_and_blocks_missing_write_preconditions(runtime) -> None:
    service = runtime.begin_turn(session_id="s", turn_id="t", user_message="Inspect")
    read = service.gate_action("read_file", {"path": "x"})
    assert read.outcome is GateOutcome.ALLOW
    write = service.gate_action("write_file", {"path": "x", "content": "data"})
    assert write.outcome is GateOutcome.BLOCK
    assert write.reason_code == "MISSING_PRECONDITION"
    assert write.suggested_observation


def test_unknown_mutation_blocks_conservatively(runtime) -> None:
    service = runtime.begin_turn(session_id="s", turn_id="t", user_message="Inspect")
    decision = service.gate_action("frobnicate", {"operation": "publish", "target": "prod"})
    assert decision.outcome is GateOutcome.BLOCK
    assert decision.reason_code == "UNKNOWN_EFFECTFUL_TOOL"
