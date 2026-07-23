from __future__ import annotations

import copy
import inspect
import json
import os
from pathlib import Path, PurePath
from types import SimpleNamespace

import pytest
import yaml

from belief_ledger_pramana.config import (
    PLUGIN_STATE_DIR,
    ConfigError,
    packaged_yaml,
    validate_config,
)
from belief_ledger_pramana.gate.classify import ActionPolicyRegistry
from belief_ledger_pramana.models import GateOutcome, Health, Stakes, VerificationMethod
from belief_ledger_pramana.runtime import (
    PluginRuntime,
    RuntimeUnavailable,
    _contradiction_payload,
    _safe_text_hash,
)
from belief_ledger_pramana.store import LedgerStore


def test_correctness_sensitive_belief_reads_have_no_implicit_limit() -> None:
    parameter = inspect.signature(LedgerStore.list_beliefs).parameters["limit"]
    assert parameter.default is None


def test_startup_fails_closed_when_a_projection_is_tampered(
    tmp_path: Path, fake_ctx: object, compatibility: object
) -> None:
    runtime = PluginRuntime(fake_ctx, compatibility=compatibility, hermes_home=tmp_path)
    runtime.ensure_initialized()
    service = runtime.begin_turn(session_id="projection", turn_id="one", user_message="Observe")
    service.ingest_user_message("Atlas is healthy.", session_id="projection", turn_id="one")
    assert runtime.store is not None
    with runtime.store.connect() as connection:
        connection.execute(
            "UPDATE beliefs SET status='out' WHERE episode_id=?", (service.episode_id,)
        )

    restarted = PluginRuntime(fake_ctx, compatibility=compatibility, hermes_home=tmp_path)
    with pytest.raises(RuntimeUnavailable, match="projection replay mismatch"):
        restarted.ensure_initialized()


def test_invalid_config_is_reloaded_after_it_is_repaired(
    tmp_path: Path, fake_ctx: object, compatibility: object
) -> None:
    root = tmp_path / PLUGIN_STATE_DIR
    root.mkdir()
    config_path = root / "config.yaml"
    config_path.write_text("mode: unsafe\n", encoding="utf-8")
    if os.name != "nt":
        config_path.chmod(0o600)
    runtime = PluginRuntime(fake_ctx, compatibility=compatibility, hermes_home=tmp_path)
    runtime.ensure_initialized()

    assert runtime.health is Health.DEGRADED
    assert runtime.config.source == config_path.resolve()
    previous_mtime = runtime.config.mtime_ns

    config_path.write_text(
        yaml.safe_dump(packaged_yaml("defaults.yaml"), sort_keys=False), encoding="utf-8"
    )
    assert previous_mtime is not None
    repaired_mtime = max(config_path.stat().st_mtime_ns, previous_mtime + 1_000_000_000)
    os.utime(config_path, ns=(repaired_mtime, repaired_mtime))

    runtime._reload_at_boundary()

    assert runtime.health is Health.HEALTHY
    assert not any("configuration" in reason for reason in runtime.health_reasons)
    assert runtime.config.mtime_ns == repaired_mtime


def test_tool_recheck_uses_the_task_belief_not_the_last_belief(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = runtime.begin_turn(session_id="recheck", turn_id="one", user_message="Observe")
    monkeypatch.setattr(service, "_after_new_beliefs", lambda: None)
    service.ingest_tool_result("opaque_probe", {}, "first", status="success")
    original = next(
        belief
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.content.startswith("Hermes observed opaque_probe")
    )
    task, _ = service.request_verification(original.id, VerificationMethod.TOOL_RECHECK)
    service.ingest_tool_result("opaque_probe", {}, "second", status="success")
    service.ingest_tool_result("unrelated_probe", {}, "third", status="success")

    assert service._complete_passive_tasks()
    completed = service.store.get_verification_task(task.id)
    assert (
        completed is not None and completed.state == "completed" and completed.result == "confirmed"
    )


def test_semantic_contradiction_processing_advances_past_resolved_pairs(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = runtime.begin_turn(session_id="semantic", turn_id="one", user_message="Compare")
    monkeypatch.setattr(service, "_after_new_beliefs", lambda: None)
    for message in (
        "Service Atlas is healthy.",
        "Service Atlas is down.",
        "Node Borealis is healthy.",
        "Node Borealis is down.",
    ):
        service.ingest_user_message(message, session_id="semantic", turn_id="one")

    seen: list[tuple[str, str]] = []

    def record_compatible(left, right, existing):
        del existing
        seen.append((left.id, right.id))
        payload = _contradiction_payload(left, right)
        return service._component_verdict_drafts(
            "contradiction_classifier",
            _safe_text_hash(payload),
            "compatible",
            {"left": left.id, "right": right.id},
            premise_ids=(left.id, right.id),
        )

    monkeypatch.setattr(service, "_semantic_contradiction_drafts", record_compatible)
    assert service._detect_deterministic_rebuts()
    assert service._detect_deterministic_rebuts()
    assert len(seen) == 2
    assert seen[0] != seen[1]


class _NoUsageLlm:
    def complete_structured(self, **kwargs: object) -> object:
        del kwargs
        return SimpleNamespace(parsed={"ok": True}, provider="test", model="test-model", usage=None)


def test_missing_provider_usage_consumes_the_reserved_budget(runtime) -> None:
    service = runtime.begin_turn(session_id="usage", turn_id="one", user_message="Budget")
    runtime.ctx._llm = _NoUsageLlm()

    result = service.llm.complete_structured(
        episode_id=service.episode_id,
        purpose="belief-ledger.test",
        instructions="classify",
        text="payload",
        schema={"type": "object"},
        schema_name="test",
        max_tokens=7,
        validator=lambda value: value,
    )

    episode = service.store.get_episode(service.episode_id)
    assert result.input_tokens > 0 and result.output_tokens == 7
    assert episode is not None
    assert episode.input_tokens_used == result.input_tokens
    assert episode.output_tokens_used == 7


def test_target_bound_structured_observations_satisfy_default_write_preconditions(runtime) -> None:
    service = runtime.begin_turn(session_id="gate", turn_id="one", user_message="Prepare write")
    parent = "/workspace/config"
    target = f"{parent}/settings.yaml"
    service.ingest_tool_result(
        "list_directory",
        {"path": parent},
        json.dumps({"path": parent, "entries": []}),
        status="success",
    )
    service.ingest_tool_result(
        "environment_identity",
        {},
        json.dumps({"environment_id": "workspace-a"}),
        status="success",
    )

    parent_proposition = f"Parent {PurePath(target).parent} exists"
    observations = {
        belief.content: belief.status.value
        for belief in service.store.list_beliefs(service.episode_id)
    }
    assert observations.get(parent_proposition) == "in"
    assert observations.get("The current execution environment is identified") == "in"

    decision = service.gate_action("write_file", {"path": target, "content": "enabled: true"})
    assert decision.outcome is GateOutcome.ALLOW


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda config: config["context"].update(relevance="unknown"), "context.relevance"),
        (lambda config: config["lint"].update(pending_marker="broken\nmarker"), "pending_marker"),
    ],
)
def test_config_rejects_silent_relevance_fallbacks_and_invalid_markers(
    mutate, message: str
) -> None:
    config = copy.deepcopy(packaged_yaml("defaults.yaml"))
    mutate(config)
    with pytest.raises(ConfigError, match=message):
        validate_config(config)


def test_action_policy_rejects_string_booleans() -> None:
    policy = copy.deepcopy(packaged_yaml("action-policies.yaml"))
    policy["rules"][0]["effectful"] = "false"
    with pytest.raises(ValueError, match="effectful must be a boolean"):
        ActionPolicyRegistry(policy)


def test_duplicate_observation_refreshes_evidence_without_duplicating_belief(runtime) -> None:
    service = runtime.begin_turn(session_id="duplicates", turn_id="one", user_message="Observe")
    service.ingest_user_message("Atlas is healthy.", sender_id="same-source")
    first = service.store.list_beliefs(service.episode_id)
    assert len(first) == 1

    service.ingest_user_message("Atlas is healthy.", sender_id="same-source")

    beliefs = service.store.list_beliefs(service.episode_id)
    evidence = [
        item
        for item in service.store.events(service.episode_id)
        if item.kind == "EVIDENCE_INGESTED"
    ]
    event_kinds = [item.kind for item in service.store.events(service.episode_id)]
    assert len(beliefs) == 1
    assert len(beliefs[0].evidence) == 2
    assert len(evidence) == 2
    assert "DUPLICATE_CONTENT_OBSERVED" in event_kinds
    assert "BELIEF_OBSERVATION_REFRESHED" in event_kinds


def test_callbacks_without_stable_ids_are_not_accidentally_deduplicated(runtime) -> None:
    service = runtime.begin_turn(session_id="anonymous-callback", turn_id="one", user_message="Run")
    for _ in range(2):
        service.ingest_tool_result("opaque_probe", {}, "same output", status="success")

    ingested = [
        item
        for item in service.store.events(service.episode_id)
        if item.kind == "EVIDENCE_INGESTED"
    ]
    assert len(ingested) == 2


def test_raising_stakes_supersedes_understrength_verification_tasks(runtime) -> None:
    service = runtime.begin_turn(session_id="stakes", turn_id="one", user_message="Assess")
    service.ingest_user_message(
        "Atlas is healthy.",
        sender_id="operator",
        session_id="stakes",
        turn_id="one",
    )
    service.set_stakes(Stakes.HIGH, user_initiated=True)
    original = service.store.list_verification_tasks(service.episode_id, state="open")
    assert len(original) == 1 and original[0].k_required == 1

    service.set_stakes(Stakes.CRITICAL, user_initiated=True)

    completed = service.store.get_verification_task(original[0].id)
    replacement = service.store.list_verification_tasks(service.episode_id, state="open")
    assert completed is not None and completed.result == "superseded"
    assert len(replacement) == 1 and replacement[0].k_required == 2
