from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from belief_ledger_core import (
    ActionPermit,
    ApprovalResult,
    BeliefLedger,
    BeliefLedgerError,
    CoreConfigSnapshot,
    EnforcementProfile,
    EpisodeContext,
    EvidenceObservation,
    HostCapabilities,
    OutputCandidate,
    ToolDescriptor,
    ToolInvocation,
    ToolPolicyManifest,
    deterministic_dependencies,
)
from belief_ledger_core.events import EventDraft, canonical_json, content_hash, to_primitive
from belief_ledger_core.models import VerificationMethod
from belief_ledger_core.verification.scheduler import VerificationScheduler


def _manifest(
    *,
    revision: str = "v1",
    preconditions: tuple[str, ...] = (),
    minimum_integrity: str = "untrusted",
) -> dict:
    return {
        "schema_version": 2,
        "rules": [
            {
                "id": "mutate",
                "revision": revision,
                "effectful": True,
                "base_stakes": "high",
                "exact": ["mutate"],
                "target_fields": ["recipient"],
                "preconditions": list(preconditions),
                "approval_policy": "none",
                "minimum_source_integrity": minimum_integrity,
                "canonicalization_version": 1,
            }
        ],
    }


def _action_ledger(tmp_path: Path, *, manifest: dict | None = None) -> BeliefLedger:
    return BeliefLedger.open(
        state_root=tmp_path,
        dependencies=deterministic_dependencies(),
        capabilities=HostCapabilities(pre_action_gate=True),
        requested_profile=EnforcementProfile.ACTION_ENFORCE,
        manifest=manifest or _manifest(),
    )


def _context(label: str = "s") -> EpisodeContext:
    return EpisodeContext.normalize(session_id=label, turn_id="t", task_id=label)


def _simulate_partial_finalize(ledger: BeliefLedger, episode_id: str) -> None:
    """Commit the lifecycle event without the revocation that normally follows it.

    This is the state a crash between `finalize_episode`'s two transactions leaves behind.
    """

    ledger.store.append_events(
        episode_id,
        [
            EventDraft(
                "EPISODE_FINALIZED",
                "episode",
                episode_id,
                {
                    "state": "finalized",
                    "episode_key": f"closed:{episode_id}",
                    "updated_at": ledger.dependencies.clock.now(),
                },
            )
        ],
    )


def _permit_on_a_fresh_episode(ledger: BeliefLedger) -> tuple[str, ActionPermit, ToolInvocation]:
    context = _context()
    episode = ledger.start_episode(context)
    ledger.ingest_evidence(
        episode.id, EvidenceObservation.normalize("A current fact", source_name="probe")
    )
    invocation = ToolInvocation.normalize(context, "mutate", {"recipient": "42"})
    permit = ledger.evaluate_action(episode.id, invocation).permit
    assert permit is not None
    return episode.id, permit, invocation


def test_effectful_permissions_fail_closed_in_observe_and_on_policy_or_config_drift(
    tmp_path: Path,
) -> None:
    context = _context()
    invocation = ToolInvocation.normalize(context, "mutate", {"recipient": "42"})
    observe = BeliefLedger.open(state_root=tmp_path / "observe", manifest=_manifest())
    observe_episode = observe.start_episode(context)
    blocked = observe.evaluate_action(observe_episode.id, invocation)
    assert blocked.permit is None
    assert blocked.reason_code == "PROFILE_DOES_NOT_ENFORCE_ACTIONS"

    policy_ledger = _action_ledger(tmp_path / "policy")
    policy_episode = policy_ledger.start_episode(context)
    policy_permit = policy_ledger.evaluate_action(policy_episode.id, invocation).permit
    assert policy_permit is not None
    changed_manifest = _manifest(minimum_integrity="trusted")
    policy_ledger.manifest = ToolPolicyManifest.load(changed_manifest)
    assert (
        policy_ledger.consume_permission(policy_permit, invocation).reason_code
        == "POLICY_CONTENT_DRIFT"
    )

    config_ledger = _action_ledger(tmp_path / "config")
    config_episode = config_ledger.start_episode(context)
    config_permit = config_ledger.evaluate_action(config_episode.id, invocation).permit
    assert config_permit is not None
    changed_config = to_primitive(config_ledger.config.data)
    changed_config["enabled"] = False
    config_ledger.config = CoreConfigSnapshot(1, config_ledger.state_root, None, changed_config, "")
    assert (
        config_ledger.consume_permission(config_permit, invocation).reason_code
        == "CONFIG_CONTENT_DRIFT"
    )


def test_consume_rechecks_support_inside_the_authorization_database_transaction(
    tmp_path: Path,
) -> None:
    ledger = _action_ledger(
        tmp_path,
        manifest=_manifest(preconditions=("recipient_identity",), minimum_integrity="trusted"),
    )
    context = _context()
    episode = ledger.start_episode(context)
    admission = ledger.ingest_direct_observation(
        episode.id,
        EvidenceObservation.normalize(
            "Precondition recipient_identity holds for 42",
            source_name="directory",
            source_integrity="trusted",
            target="42",
        ),
    )
    invocation = ToolInvocation.normalize(context, "mutate", {"recipient": "42"})
    permit = ledger.evaluate_action(episode.id, invocation).permit
    assert permit is not None

    ledger.store.append_events(
        episode.id,
        [
            EventDraft(
                "BELIEF_STATUS_CHANGED",
                "belief",
                admission.belief_id,
                {"from": "in", "to": "out", "cause": "concurrent_retraction"},
            )
        ],
    )
    consumed = ledger.consume_permission(permit, invocation)
    assert not consumed.consumed and consumed.reason_code == "SUPPORT_RETRACTED"
    assert ledger.enforcement.action_state(permit.decision_id) == "revoked"


def test_ingestion_is_validated_idempotent_and_uses_canonical_public_ids(tmp_path: Path) -> None:
    ledger = BeliefLedger.open(
        state_root=tmp_path,
        dependencies=deterministic_dependencies(),
    )
    episode = ledger.start_episode(_context())
    observation = EvidenceObservation.normalize(
        "A current measured fact",
        source_name="probe",
        correlation={"idempotency_key": "request-1"},
    )
    first = ledger.ingest_evidence(episode.id, observation)
    second = ledger.ingest_evidence(episode.id, observation)
    assert second == first
    assert first.belief_id.startswith("b_")
    assert first.evidence_id.startswith("e_")
    with pytest.raises(BeliefLedgerError) as reused:
        ledger.ingest_evidence(episode.id, replace(observation, content="A different fact"))
    assert reused.value.reason_code == "IDEMPOTENCY_KEY_REUSED"

    with pytest.raises(BeliefLedgerError) as changed_source:
        ledger.ingest_evidence(
            episode.id,
            EvidenceObservation.normalize(
                "Another measured fact",
                source_name="renamed-probe",
                provenance_root="probe",
                source_integrity="trusted",
            ),
        )
    assert changed_source.value.reason_code == "SOURCE_METADATA_MISMATCH"

    with pytest.raises(BeliefLedgerError) as missing_content:
        ledger.ingest_evidence(
            episode.id,
            EvidenceObservation.normalize(
                "sensitive payload",
                source_name="private-probe",
                retention_mode="hash_only",
            ),
        )
    assert missing_content.value.reason_code == "HASH_ONLY_REQUIRES_BELIEF_CONTENT"
    hash_only = ledger.ingest_evidence(
        episode.id,
        EvidenceObservation.normalize(
            "sensitive payload",
            source_name="private-probe",
            retention_mode="hash_only",
            belief_content="The private probe completed successfully",
        ),
    )
    assert hash_only.status == "in"

    with pytest.raises(BeliefLedgerError) as missing_derivation:
        ledger.ingest_derived_evidence(
            episode.id,
            EvidenceObservation.normalize("A conclusion", source_name="model"),
        )
    assert missing_derivation.value.reason_code == "MISSING_DERIVATION"


def test_untrusted_shabda_is_pending_or_quarantined_and_pending_output_is_marked(
    tmp_path: Path,
) -> None:
    ledger = BeliefLedger.open(state_root=tmp_path)
    context = _context()
    episode = ledger.start_episode(context)
    pending = ledger.ingest_evidence(
        episode.id,
        EvidenceObservation.normalize(
            "The service release is current",
            source_name="unknown-web",
            source_kind="web",
            source_integrity="untrusted",
            provenance_root="https://unknown.invalid/current",
            pramana="shabda",
            stakes="med",
        ),
    )
    assert pending.status == "pending"
    assert ledger.store.list_verification_tasks(episode.id, state="open")
    unmarked = ledger.evaluate_output(
        episode.id,
        OutputCandidate(
            1,
            context,
            f"The service release is current [{pending.belief_id}].",
            "high",
        ),
    )
    assert not unmarked.accepted
    marked = ledger.evaluate_output(
        episode.id,
        OutputCandidate(
            1,
            context,
            f"The service release is current [{pending.belief_id}] (unverified).",
            "high",
        ),
    )
    assert marked.accepted

    quarantined = ledger.ingest_evidence(
        episode.id,
        EvidenceObservation.normalize(
            "The critical release is current",
            source_name="unknown-critical-web",
            source_kind="web",
            source_integrity="untrusted",
            provenance_root="https://unknown.invalid/critical",
            pramana="shabda",
            stakes="critical",
        ),
    )
    assert quarantined.status == "quarantined"
    assert len(ledger.store.list_verification_tasks(episode.id, state="open")) == 2


def test_output_contract_blocks_empty_short_or_invalid_high_stakes_candidates(
    tmp_path: Path,
) -> None:
    ledger = BeliefLedger.open(state_root=tmp_path)
    context = _context()
    episode = ledger.start_episode(context)
    admitted = ledger.ingest_direct_observation(
        episode.id,
        EvidenceObservation.normalize("System Atlas is ready", source_name="probe"),
    )
    accepted = ledger.evaluate_output(
        episode.id,
        OutputCandidate(
            1,
            context,
            f"System Atlas is ready [{admitted.belief_id}].",
            "high",
        ),
    )
    assert accepted.accepted
    assert not ledger.evaluate_output(
        episode.id, OutputCandidate(1, context, "Ready", "critical")
    ).accepted
    annotated = ledger.evaluate_output(
        episode.id, OutputCandidate(1, context, "An unsupported claim exists.", "low")
    )
    assert annotated.accepted and b"Grounding warning" in annotated.payload
    for candidate, reason in (
        (OutputCandidate(1, context, "", "high"), "INVALID_OUTPUT"),
        (OutputCandidate(1, context, "Text", "absolute"), "INVALID_STAKES"),
        (OutputCandidate(1, context, "Text", "high", False), "NON_FINAL_OUTPUT"),
    ):
        with pytest.raises(BeliefLedgerError) as invalid:
            ledger.evaluate_output(episode.id, candidate)
        assert invalid.value.reason_code == reason


def test_finalization_revokes_permissions_rejects_mutation_and_rotates_episode_key(
    tmp_path: Path,
) -> None:
    ledger = _action_ledger(tmp_path)
    context = _context()
    episode = ledger.start_episode(context)
    observation = EvidenceObservation.normalize("A current fact", source_name="probe")
    admitted = ledger.ingest_evidence(episode.id, observation)
    invocation = ToolInvocation.normalize(context, "mutate", {"recipient": "42"})
    permit = ledger.evaluate_action(episode.id, invocation).permit
    assert permit is not None
    ledger.finalize_episode(episode.id)
    assert ledger.consume_permission(permit, invocation).reason_code == "TOKEN_REVOKED"

    operations = (
        lambda: ledger.ingest_evidence(episode.id, observation),
        lambda: ledger.retract_evidence(episode.id, admitted.belief_id),
        lambda: ledger.evaluate_action(episode.id, invocation),
        lambda: ledger.evaluate_output(
            episode.id, OutputCandidate(1, context, "A current fact", "high")
        ),
        lambda: ledger.record_approval(
            episode.id,
            ApprovalResult(
                1,
                context,
                True,
                "",
                "mutate",
                content_hash(canonical_json(invocation.arguments_dict())),
                "42",
                "mutate",
                "v1",
                "exact_action",
            ),
        ),
    )
    for operation in operations:
        with pytest.raises(BeliefLedgerError) as finalized:
            operation()
        assert finalized.value.reason_code == "EPISODE_FINALIZED"
    replacement = ledger.start_episode(context)
    assert replacement.id != episode.id and replacement.state == "active"


def test_permit_is_rejected_after_episode_finalization(tmp_path: Path) -> None:
    ledger = _action_ledger(tmp_path)
    episode_id, permit, invocation = _permit_on_a_fresh_episode(ledger)
    ledger.finalize_episode(episode_id)

    # A completed finalize already revoked the permit, so consumption is refused by the
    # decision-state check before the in-transaction episode check is reached. The episode
    # check is what covers a finalize whose revocation never ran.
    consumed = ledger.consume_permission(permit, invocation)
    assert not consumed.consumed and consumed.reason_code == "TOKEN_REVOKED"
    assert ledger.enforcement.action_state(permit.decision_id) == "revoked"


def test_permit_is_rejected_when_finalize_revocation_did_not_run(tmp_path: Path) -> None:
    ledger = _action_ledger(tmp_path)
    episode_id, permit, invocation = _permit_on_a_fresh_episode(ledger)
    _simulate_partial_finalize(ledger, episode_id)
    assert ledger.enforcement.action_state(permit.decision_id) == "issued"

    consumed = ledger.consume_permission(permit, invocation)
    assert not consumed.consumed and consumed.reason_code == "EPISODE_FINALIZED"
    assert ledger.enforcement.action_state(permit.decision_id) == "revoked"


def test_finalize_is_idempotent_and_repairs_missing_revocation(tmp_path: Path) -> None:
    ledger = _action_ledger(tmp_path)
    episode_id, permit, invocation = _permit_on_a_fresh_episode(ledger)
    _simulate_partial_finalize(ledger, episode_id)
    assert ledger.enforcement.action_state(permit.decision_id) == "issued"

    repaired = ledger.finalize_episode(episode_id)
    assert repaired.state == "finalized"
    assert ledger.enforcement.action_state(permit.decision_id) == "revoked"

    assert ledger.finalize_episode(episode_id).state == "finalized"
    assert ledger.enforcement.action_state(permit.decision_id) == "revoked"
    assert ledger.consume_permission(permit, invocation).reason_code == "TOKEN_REVOKED"
    assert ledger.verify_chain().valid


def test_to_primitive_never_serializes_the_raw_permit_token(tmp_path: Path) -> None:
    ledger = _action_ledger(tmp_path)
    context = _context()
    episode = ledger.start_episode(context)
    ledger.ingest_evidence(
        episode.id, EvidenceObservation.normalize("A current fact", source_name="probe")
    )
    invocation = ToolInvocation.normalize(context, "mutate", {"recipient": "42"})
    authorization = ledger.evaluate_action(episode.id, invocation)
    permit = authorization.permit
    assert permit is not None
    raw_token = permit._raw_token
    assert raw_token

    for serialized in (
        canonical_json(to_primitive(authorization)),
        canonical_json(to_primitive(permit)),
        repr(permit),
    ):
        assert raw_token not in serialized
        assert "_raw_token" not in serialized


def test_unrelated_open_conflict_in_the_episode_blocks_permit_consumption(tmp_path: Path) -> None:
    """The conflict check is episode-wide on purpose, not scoped to blocking_conflict_ids.

    A conflict opened after the permit was issued is exactly the case the binding could not
    have named, so this pins the intent rather than leaving it incidental.
    """

    ledger = _action_ledger(tmp_path)
    episode_id, permit, invocation = _permit_on_a_fresh_episode(ledger)
    assert permit.binding.blocking_conflict_ids == ()

    left = ledger.ingest_direct_observation(
        episode_id, EvidenceObservation.normalize("An unrelated left claim", source_name="left")
    )
    right = ledger.ingest_direct_observation(
        episode_id, EvidenceObservation.normalize("An unrelated right claim", source_name="right")
    )
    scheduler = VerificationScheduler(ledger.store, dict(ledger.config.data))
    task = scheduler.request(episode_id, left.belief_id, VerificationMethod.CROSS_SOURCE).task
    ledger.store.append_events(
        episode_id,
        [
            EventDraft(
                "CONFLICT_OPENED",
                "conflict",
                "cf_unrelated_0001",
                {
                    "record": {
                        "id": "cf_unrelated_0001",
                        "episode_id": episode_id,
                        "left_belief_id": left.belief_id,
                        "right_belief_id": right.belief_id,
                        "normalized_scope": {},
                        "verification_task_id": task.id,
                        "state": "open",
                    }
                },
            )
        ],
    )

    consumed = ledger.consume_permission(permit, invocation)
    assert not consumed.consumed and consumed.reason_code == "OPEN_CONFLICT"
    assert ledger.enforcement.action_state(permit.decision_id) == "revoked"


def test_config_paths_and_recursive_values_fail_closed(tmp_path: Path) -> None:
    for config in (
        {"unknown": True},
        {"storage": {"busy_timeout_ms": 0}},
        {"trust": {"apta": {"floor": 0.9, "ceiling": 0.1}}},
        {"enforcement": {"requested_profile": "absolute"}},
        {"gating": {"policy_files": [""]}},
        {"priority": {"integrity_rank": {"trusted": 0, "semi": 1, "untrusted": 2}}},
        {"priority": {"reliability_bands": {"medium": 0.9, "high": 0.1}}},
        {"priority": {"specificity_keys": ["scope", "scope"]}},
        {"trust": {"source_profile_files": "not-a-list"}},
        {"trust": {"matrix": {"pratyaksha_tool": {"low": {"mode": "invalid"}}}}},
        {"perishability_ttl": {"live_seconds": -1}},
    ):
        with pytest.raises(BeliefLedgerError) as invalid:
            BeliefLedger.open(
                state_root=tmp_path / content_hash(canonical_json(config)), config=config
            )
        assert invalid.value.reason_code == "INVALID_CONFIG"

    with pytest.raises(BeliefLedgerError) as escaped:
        BeliefLedger.open(
            state_root=tmp_path / "escape",
            config={"storage": {"database": "../outside.sqlite3"}},
        )
    assert escaped.value.reason_code == "DATABASE_OUTSIDE_STATE_ROOT"

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(BeliefLedgerError) as root_symlink:
        BeliefLedger.open(state_root=root_link)
    assert root_symlink.value.reason_code == "STATE_ROOT_SYMLINK"

    database_root = tmp_path / "database-link"
    database_root.mkdir(mode=0o700)
    (database_root / "actual.sqlite3").touch(mode=0o600)
    (database_root / "ledger.sqlite3").symlink_to("actual.sqlite3")
    with pytest.raises(BeliefLedgerError) as database_symlink:
        BeliefLedger.open(state_root=database_root)
    assert database_symlink.value.reason_code == "DATABASE_SYMLINK"


def test_public_values_are_recursively_immutable_and_schemas_are_validated() -> None:
    schema = {
        "type": "object",
        "properties": {"recipient": {"type": "string"}},
        "required": ["recipient"],
        "additionalProperties": False,
    }
    descriptor = ToolDescriptor.create("send", schema)
    schema["properties"]["recipient"]["type"] = "integer"
    descriptor.validate_arguments({"recipient": "42"})
    with pytest.raises(ValueError):
        descriptor.validate_arguments({"recipient": 42})
    with pytest.raises(TypeError):
        dict.__setitem__(descriptor.input_schema, "type", "array")

    invocation = ToolInvocation.normalize(_context(), "send", {"nested": {"value": 1}})
    nested = invocation.arguments_dict()["nested"]
    with pytest.raises(TypeError):
        nested["value"] = 2


def test_verification_cannot_cross_episode_boundaries(tmp_path: Path) -> None:
    ledger = BeliefLedger.open(state_root=tmp_path)
    first = ledger.start_episode(_context("first"))
    second = ledger.start_episode(_context("second"))
    admission = ledger.ingest_direct_observation(
        first.id, EvidenceObservation.normalize("A current fact", source_name="probe")
    )
    second_admission = ledger.ingest_direct_observation(
        second.id, EvidenceObservation.normalize("A different fact", source_name="probe")
    )
    scheduler = VerificationScheduler(ledger.store, dict(ledger.config.data))
    with pytest.raises(ValueError, match="requested episode"):
        scheduler.request(second.id, admission.belief_id, VerificationMethod.CROSS_SOURCE)
    task = scheduler.request(first.id, admission.belief_id, VerificationMethod.CROSS_SOURCE).task
    with pytest.raises(ValueError, match="event episode"):
        ledger.store.append_events(
            second.id,
            [
                EventDraft(
                    "CONFLICT_OPENED",
                    "conflict",
                    "cf_cross_episode_0001",
                    {
                        "record": {
                            "id": "cf_cross_episode_0001",
                            "episode_id": second.id,
                            "left_belief_id": admission.belief_id,
                            "right_belief_id": second_admission.belief_id,
                            "normalized_scope": {},
                            "verification_task_id": task.id,
                            "state": "open",
                        }
                    },
                )
            ],
        )


def test_purge_removes_authorization_audit_for_the_selected_episode(tmp_path: Path) -> None:
    ledger = _action_ledger(tmp_path)
    first_context = _context("first")
    second_context = _context("second")
    first = ledger.start_episode(first_context)
    second = ledger.start_episode(second_context)
    first_permit = ledger.evaluate_action(
        first.id, ToolInvocation.normalize(first_context, "mutate", {"recipient": "1"})
    ).permit
    second_permit = ledger.evaluate_action(
        second.id, ToolInvocation.normalize(second_context, "mutate", {"recipient": "2"})
    ).permit
    assert first_permit is not None and second_permit is not None

    ledger.store.purge_episode(first.id, confirmation=first.id)
    with closing(sqlite3.connect(tmp_path / "ledger.sqlite3")) as connection:
        payloads = "\n".join(
            str(row[0])
            for row in connection.execute(
                "SELECT payload_json FROM enforcement_events ORDER BY seq"
            )
        )
    assert first.id not in payloads
    assert second.id in payloads
    reopened = _action_ledger(tmp_path)
    assert reopened.verify_chain().valid
    assert reopened.replay().deterministic
