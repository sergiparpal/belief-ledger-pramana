from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from belief_ledger_core import (
    ApprovalResult,
    BeliefLedger,
    BeliefLedgerError,
    EnforcementProfile,
    EpisodeContext,
    EvidenceObservation,
    HostCapabilities,
    OutputCandidate,
    ToolInvocation,
    deterministic_dependencies,
)
from belief_ledger_core.events import canonical_json, content_hash


def _manifest() -> dict:
    return {
        "schema_version": 2,
        "rules": [
            {
                "id": "message",
                "revision": "v1",
                "effectful": True,
                "base_stakes": "high",
                "exact": ["send_customer_message"],
                "namespace": "crm",
                "target_fields": ["recipient"],
                "preconditions": ["recipient_identity"],
                "approval_policy": "required",
                "minimum_source_integrity": "trusted",
                "canonicalization_version": 1,
            },
            {
                "id": "read",
                "revision": "v1",
                "effectful": False,
                "base_stakes": "low",
                "exact": ["lookup_customer"],
                "namespace": "crm",
                "target_fields": ["recipient"],
                "preconditions": [],
                "approval_policy": "none",
                "minimum_source_integrity": "untrusted",
                "canonicalization_version": 1,
            },
        ],
    }


def test_generic_lifecycle_evidence_action_approval_retraction_and_replay(tmp_path: Path) -> None:
    ledger = BeliefLedger.open(
        state_root=tmp_path,
        dependencies=deterministic_dependencies(),
        capabilities=HostCapabilities(pre_action_gate=True),
        requested_profile=EnforcementProfile.ACTION_ENFORCE,
        manifest=_manifest(),
    )
    context = EpisodeContext.normalize(
        session_id="s", turn_id="t", task_id="customer-contact", platform="test"
    )
    episode = ledger.start_episode(context)
    read = ledger.evaluate_action(
        episode.id,
        ToolInvocation.normalize(context, "lookup_customer", {"recipient": "42"}, namespace="crm"),
    )
    assert read.outcome == "allow" and read.permit is None

    invocation = ToolInvocation.normalize(
        context,
        "send_customer_message",
        {"recipient": "42", "body": "hello"},
        namespace="crm",
    )
    assert ledger.evaluate_action(episode.id, invocation).reason_code == "MISSING_PRECONDITION"
    admitted = ledger.ingest_direct_observation(
        episode.id,
        EvidenceObservation.normalize(
            "Precondition recipient_identity holds for 42",
            source_name="directory",
            source_integrity="trusted",
            target="42",
        ),
    )
    assert ledger.evaluate_action(episode.id, invocation).reason_code == "APPROVAL_REQUIRED"
    arguments_hash = content_hash(canonical_json(invocation.arguments_dict()))
    ledger.record_approval(
        episode.id,
        ApprovalResult(
            1,
            context,
            True,
            "crm",
            invocation.name,
            arguments_hash,
            "42",
            "message",
            "v1",
            "exact_action",
        ),
    )
    allowed = ledger.evaluate_action(episode.id, invocation)
    assert allowed.permit is not None
    assert ledger.consume_permission(allowed.permit, invocation).consumed
    assert ledger.consume_permission(allowed.permit, invocation).reason_code == "TOKEN_CONSUMED"
    explanation = ledger.explain_decision(episode.id, allowed.decision_id)
    assert explanation.supports[0]["id"] == admitted.belief_id
    assert "token" not in canonical_json(explanation)

    second = ledger.evaluate_action(episode.id, invocation)
    assert second.reason_code == "APPROVAL_REQUIRED"  # exact approval was single use
    ledger.retract_evidence(episode.id, admitted.belief_id)
    assert ledger.evaluate_action(episode.id, invocation).reason_code == "MISSING_PRECONDITION"
    assert ledger.verify_chain().valid
    assert ledger.replay().deterministic


def test_secrets_and_raw_permits_are_not_persisted_and_output_is_decision_only(
    tmp_path: Path,
) -> None:
    ledger = BeliefLedger.open(state_root=tmp_path, manifest=_manifest())
    context = EpisodeContext.normalize(session_id="s2", turn_id="t2")
    episode = ledger.start_episode(context)
    admission = ledger.ingest_user_evidence(
        episode.id, "Authorization: Bearer secret-value", sender="person"
    )
    assert admission.redacted
    database_bytes = (tmp_path / "ledger.sqlite3").read_bytes()
    assert b"secret-value" not in database_bytes
    output = ledger.evaluate_output(
        episode.id,
        OutputCandidate(1, context, "Unsupported consequential claim.", "high"),
    )
    assert not output.accepted and output.payload.startswith(b"Response blocked")
    with closing(sqlite3.connect(tmp_path / "ledger.sqlite3")) as connection:
        persisted = " ".join(
            str(item)
            for row in connection.execute("SELECT * FROM action_decisions")
            for item in row
        )
    assert "deterministic-token" not in persisted
    assert not (tmp_path / "authorization.sqlite3").exists()


def test_public_observation_validation_has_a_stable_reason_code() -> None:
    with pytest.raises(BeliefLedgerError) as invalid:
        EvidenceObservation.normalize("", source_name="")
    assert invalid.value.reason_code == "INVALID_EVIDENCE_OBSERVATION"
