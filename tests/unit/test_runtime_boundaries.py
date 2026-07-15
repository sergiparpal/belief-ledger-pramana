from __future__ import annotations

import copy
import os
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from belief_ledger_pramana.config import load_config, packaged_yaml
from belief_ledger_pramana.ingestion.adapters import SourceDescriptor
from belief_ledger_pramana.models import (
    CompatibilityMode,
    DefeatKind,
    Health,
    Integrity,
    Pramana,
    SourceKind,
    Stakes,
    Status,
    VerificationMethod,
)
from belief_ledger_pramana.runtime import (
    EpisodeResolutionError,
    PluginRuntime,
    RuntimeUnavailable,
    _action_policy_data,
    _apply_source_profile,
    _validate_claim_result,
    _validate_contradiction,
    _validate_entailment,
    _validate_rewrite,
)


def test_structured_component_validators_reject_out_of_contract_data() -> None:
    with pytest.raises(ValueError, match="claims array"):
        _validate_claim_result({})
    with pytest.raises(ValueError, match="too many"):
        _validate_claim_result({"claims": [{}] * 25})
    with pytest.raises(ValueError, match="malformed"):
        _validate_claim_result({"claims": [{}]})
    claim = {
        "content": "Atlas is active",
        "pramana": "shabda",
        "span_start": 0,
        "span_end": 16,
        "exact_excerpt": "Atlas is active.",
    }
    assert _validate_claim_result({"claims": [claim]})[0].content == "Atlas is active"

    with pytest.raises(ValueError, match="response string"):
        _validate_rewrite({"response": 1})
    with pytest.raises(ValueError, match="exceeds"):
        _validate_rewrite({"response": "x" * 16_001})
    assert _validate_rewrite({"response": "bounded"}) == "bounded"

    valid_contradiction = {
        "outcome": "rebut",
        "basis": "same scope, incompatible values",
        "left_scope": {"as_of": "2026"},
        "right_scope": {"as_of": "2026"},
    }
    assert _validate_contradiction(valid_contradiction)["outcome"] == "rebut"
    for invalid in (
        [],
        {**valid_contradiction, "outcome": "guess"},
        {**valid_contradiction, "basis": ""},
        {**valid_contradiction, "left_scope": []},
        {**valid_contradiction, "right_scope": {"x": 1}},
    ):
        with pytest.raises(ValueError):
            _validate_contradiction(invalid)

    allowed = {(0, "b_allowed")}
    valid_entailment = {
        "pairs": [
            {
                "claim_index": 0,
                "belief_id": "b_allowed",
                "entailed": True,
                "basis": "x" * 400,
            }
        ]
    }
    parsed = _validate_entailment(valid_entailment, allowed)
    assert len(parsed[0]["basis"]) == 300
    for invalid in (
        {},
        {"pairs": [valid_entailment["pairs"][0]] * 31},
        {"pairs": ["bad"]},
        {
            "pairs": [
                {
                    "claim_index": 2,
                    "belief_id": "outside",
                    "entailed": "yes",
                    "basis": 1,
                }
            ]
        },
    ):
        with pytest.raises(ValueError):
            _validate_entailment(invalid, allowed)


def test_operator_policy_and_source_profile_extensions_are_versioned(tmp_path: Path) -> None:
    config = copy.deepcopy(packaged_yaml("defaults.yaml"))
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "schema_version: 1\nrules:\n"
        "  - id: custom_read\n    exact: [custom_read]\n    base_stakes: low\n"
        "    effectful: false\n    minimum_priority: untrusted\n"
        "    allow_human_approval: false\n    target_fields: []\n    preconditions: []\n",
        encoding="utf-8",
    )
    config["gating"]["policy_files"] = [str(policy)]
    policies = _action_policy_data(config)
    assert policies["rules"][0]["id"] == "custom_read"

    profile = tmp_path / "profiles.yaml"
    profile.write_text(
        "schema_version: 1\nprofiles:\n  hermes_tool:\n"
        "    kind: tool\n    integrity: semi\n    competence: {general: 0.42}\n",
        encoding="utf-8",
    )
    config["trust"]["source_profile_files"] = [str(profile)]
    descriptor = SourceDescriptor(
        SourceKind.TOOL, Integrity.TRUSTED, "probe", "tool:probe", {"general": 1.0}
    )
    changed = _apply_source_profile(descriptor, config)
    assert changed.integrity is Integrity.SEMI
    assert changed.competence == {"general": 0.42}
    retriever = SourceDescriptor(
        SourceKind.RETRIEVER, Integrity.SEMI, "index", "retriever:index", {"general": 0.5}
    )
    assert _apply_source_profile(retriever, config) is retriever

    for contents, match in (
        ("schema_version: 2\nrules: []\n", "schema"),
        ("schema_version: 1\nrules: nope\n", "rules"),
    ):
        policy.write_text(contents, encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            _action_policy_data(config)
    config["gating"]["policy_files"] = [str(tmp_path / "missing.yaml")]
    with pytest.raises(ValueError, match="unavailable"):
        _action_policy_data(config)

    config["gating"]["policy_files"] = []
    for contents, match in (
        ("schema_version: 2\nprofiles: {}\n", "schema"),
        ("schema_version: 1\nprofiles: []\n", "profiles"),
        (
            "schema_version: 1\nprofiles:\n  hermes_tool: {kind: impossible}\n",
            "invalid",
        ),
    ):
        profile.write_text(contents, encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            _apply_source_profile(descriptor, config)
    config["trust"]["source_profile_files"] = [str(tmp_path / "missing-profile.yaml")]
    with pytest.raises(ValueError, match="unavailable"):
        _apply_source_profile(descriptor, config)


def test_runtime_invalid_config_and_corrupt_database_are_fail_closed(
    tmp_path: Path, fake_ctx, compatibility
) -> None:
    invalid_home = tmp_path / "invalid"
    state = invalid_home / "belief-ledger-pramana"
    state.mkdir(parents=True)
    state.joinpath("config.yaml").write_text(
        "schema_version: 1\nmode: enforce\nunknown: true\n", encoding="utf-8"
    )
    degraded = PluginRuntime(fake_ctx, compatibility=compatibility, hermes_home=invalid_home)
    degraded.ensure_initialized()
    assert degraded.health is Health.DEGRADED
    assert degraded.config.mode == "enforce"
    assert any("invalid configuration" in reason for reason in degraded.health_reasons)

    corrupt_home = tmp_path / "corrupt"
    _, paths = load_config(hermes_home=corrupt_home)
    paths.database.write_bytes(b"not a sqlite database")
    unavailable = PluginRuntime(fake_ctx, compatibility=compatibility, hermes_home=corrupt_home)
    with pytest.raises(RuntimeUnavailable, match="database unavailable"):
        unavailable.ensure_initialized()
    assert unavailable.health is Health.UNAVAILABLE


def test_episode_resolution_reload_and_runtime_helpers(
    tmp_path: Path, fake_ctx, compatibility
) -> None:
    result = PluginRuntime(fake_ctx, compatibility=compatibility, hermes_home=tmp_path)
    with pytest.raises(EpisodeResolutionError):
        result.current_service()
    assert result.operational()
    result.compatibility = replace(result.compatibility, mode=CompatibilityMode.DIAGNOSTICS_ONLY)
    assert not result.operational()
    result.compatibility = compatibility

    task_episode = result.resolve_episode_id(task_id="task-1")
    assert result.resolve_episode_id(task_id="task-1") == task_episode
    approval_episode = result.resolve_episode_id(session_key="unbound-key")
    result.bind_approval_session_key("bound-key", task_episode)
    result.bind_approval_session_key("", approval_episode)
    assert result.resolve_episode_id(session_key="bound-key") == task_episode
    assert result.resolve_episode_id(session_key="unbound-key") == approval_episode
    assert result.resolve_episode_id() != result.resolve_episode_id()

    service = result.begin_turn(
        session_id="reload-session", turn_id="reload-one", user_message="Initial query"
    )
    assert result.resolve_episode_id(turn_id="reload-one") == service.episode_id
    assert result.query_for(service.episode_id) == "Initial query"
    result.set_recent_tool_result(service.episode_id, "x" * 3_000)
    assert len(result.recent_tool_result(service.episode_id)) == 2_000
    result.begin_turn(
        session_id="reload-session", turn_id="reload-one", user_message="Initial query"
    )

    assert result.paths is not None and result.config.source is not None
    updated = copy.deepcopy(result.config.data)
    updated["context"]["max_chars"] = 7_000
    result.config.source.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    os.utime(
        result.config.source,
        ns=(result.config.mtime_ns + 1_000_000, result.config.mtime_ns + 1_000_000),
    )
    result.begin_turn(
        session_id="reload-session", turn_id="reload-two", user_message="Reload config"
    )
    assert result.config.data["context"]["max_chars"] == 7_000

    changed_database = copy.deepcopy(result.config.data)
    changed_database["storage"]["database"] = "different.sqlite3"
    prior_mtime = result.config.source.stat().st_mtime_ns
    result.config.source.write_text(
        yaml.safe_dump(changed_database, sort_keys=False), encoding="utf-8"
    )
    os.utime(result.config.source, ns=(prior_mtime + 1_000_000, prior_mtime + 1_000_000))
    result.begin_turn(
        session_id="reload-session", turn_id="reload-three", user_message="Changed database"
    )
    assert any("restart is required" in reason for reason in result.health_reasons)

    result.finalize(service.episode_id, state="reset", turn_id="reload-three")
    assert not result.injection_failed(service.episode_id)
    rotated = result.service(session_id="reload-session", turn_id="reload-four")
    assert rotated.episode_id != service.episode_id


def test_service_boundary_validation_absence_and_manual_defeats(runtime) -> None:
    service = runtime.begin_turn(
        session_id="boundary-session",
        turn_id="boundary-turn",
        user_message="Premise Alpha is established.",
        sender_id="user",
    )
    assert service.ingest_user_message("   ") == ()
    service.ingest_user_message(
        "Premise Alpha is established.",
        session_id="boundary-session",
        turn_id="boundary-turn",
        sender_id="user",
    )
    premise = next(
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.content == "Premise Alpha is established"
    )
    with pytest.raises(ValueError, match="may only"):
        service.record_inference(
            content="invalid",
            pramana=Pramana.SHABDA,
            premise_ids=(premise.id,),
            warrant="invalid",
        )
    with pytest.raises(ValueError, match="warrant"):
        service.record_inference(
            content="invalid",
            pramana=Pramana.ANUMANA,
            premise_ids=(premise.id,),
            warrant="",
        )
    with pytest.raises(ValueError, match="at least one"):
        service.record_inference(
            content="invalid", pramana=Pramana.ANUMANA, premise_ids=(), warrant="x"
        )
    with pytest.raises(ValueError, match="does not exist"):
        service.record_inference(
            content="invalid",
            pramana=Pramana.ANUMANA,
            premise_ids=("b_missing",),
            warrant="x",
        )

    arthapatti, _ = service.record_inference(
        content="The missing cause is configuration drift",
        pramana=Pramana.ARTHAPATTI,
        premise_ids=(premise.id,),
        warrant="Configuration drift is the best recorded explanation",
        alternatives=("operator change",),
        explanandum=premise.id,
    )
    upamana, _ = service.record_inference(
        content="Incident Atlas resembles the prior staging incident",
        pramana=Pramana.UPAMANA,
        premise_ids=(premise.id,),
        warrant="The symptoms and environment match",
        similarity_basis="same symptoms and staging scope",
    )
    assert arthapatti.status is Status.IN
    assert upamana.status is Status.IN

    with pytest.raises(ValueError, match="attacker"):
        service.add_defeat("b_missing", premise.id, kind=DefeatKind.REBUT, basis="x")
    with pytest.raises(ValueError, match="target"):
        service.add_defeat(premise.id, "b_missing", kind=DefeatKind.REBUT, basis="x")
    with pytest.raises(ValueError, match="undercut target"):
        service.add_defeat(premise.id, "j_missing", kind=DefeatKind.UNDERCUT, basis="x")
    first = service.add_defeat(
        premise.id,
        arthapatti.id,
        kind=DefeatKind.REBUT,
        basis="manual contradiction",
        reciprocal_rebut=False,
    )
    assert first
    assert (
        service.add_defeat(
            premise.id,
            arthapatti.id,
            kind=DefeatKind.REBUT,
            basis="duplicate",
            reciprocal_rebut=False,
        )
        == ()
    )

    with pytest.raises(ValueError, match="belief does not exist"):
        service.request_verification("b_missing", VerificationMethod.HUMAN)
    with pytest.raises(ValueError, match="belief does not exist"):
        service.explain("b_missing")
    assert service.set_stakes(Stakes.MED, user_initiated=False) == ()
    service.set_stakes(Stakes.HIGH, user_initiated=True)
    with pytest.raises(ValueError, match="explicit user"):
        service.set_stakes(Stakes.LOW, user_initiated=False)
    with pytest.raises(ValueError, match="source descriptor"):
        service.ensure_source(None)

    service.ingest_tool_result(
        "search_files",
        {
            "query": "legacy_mode",
            "corpus": "repository",
            "scope": "src",
            "parameters": {"case_sensitive": True},
            "coverage": 0.95,
            "recall": 0.9,
            "truncated": False,
            "absence_proposition": "Legacy mode does not exist in src",
            "domain": "library_internals",
        },
        "no results",
        session_id="boundary-session",
        turn_id="boundary-turn",
        tool_call_id="qualified-negative",
        status="success",
    )
    absence = next(
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.pramana is Pramana.ANUPALABDHI
    )
    assert absence.status is Status.PENDING
    assert any(
        task.belief_id == absence.id and task.method is VerificationMethod.TOOL_RECHECK
        for task in service.store.list_verification_tasks(service.episode_id, state="open")
    )

    service.ingest_tool_result(
        "opaque_probe",
        {},
        "api_key=super-secret-value",
        session_id="boundary-session",
        turn_id="boundary-turn",
        tool_call_id="redacted-tool",
        status="failure",
    )
    assert any(
        event.kind == "EVIDENCE_REDACTED" for event in service.store.events(service.episode_id)
    )
    with pytest.raises(RuntimeUnavailable):
        _ = runtime.service_for_id("ep_missing").episode
