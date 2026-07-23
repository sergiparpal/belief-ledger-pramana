from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from belief_ledger_core.dependencies import (
    FakeStructuredModel,
    StructuredModelResult,
    deterministic_dependencies,
)
from belief_ledger_core.gate.classify import ActionPolicyRegistry
from belief_ledger_core.gate.decision import ActionGate
from belief_ledger_core.llm.client import HostLlmClient, LlmComponentError
from belief_ledger_core.models import CompatibilityMode, Episode, GateOutcome, Stakes
from belief_ledger_core.store import LedgerStore


def _data(name: str) -> dict[str, object]:
    value = yaml.safe_load(
        files("belief_ledger_core.data").joinpath(name).read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _store_with_episode(tmp_path: Path) -> tuple[LedgerStore, Episode]:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    now = datetime(2026, 7, 23, tzinfo=UTC)
    episode = Episode(
        "episode-core-services",
        "session:core-services",
        "core-services",
        "",
        "test",
        "fake",
        Stakes.MED,
        1,
        now,
        now,
        CompatibilityMode.FULL,
    )
    store.create_episode(episode)
    return store, episode


def test_core_action_gate_covers_absent_read_only_unknown_and_disabled_paths(
    tmp_path: Path,
) -> None:
    store, episode = _store_with_episode(tmp_path)
    config = _data("defaults.yaml")
    policies = ActionPolicyRegistry(_data("action-policies.yaml"))
    gate = ActionGate(store, config, policies)

    absent = gate.evaluate("missing", "search_web", {})
    assert absent.reason_code == "EPISODE_UNAVAILABLE"

    read_only = gate.evaluate(episode.id, "search_web", {"query": "status"})
    assert read_only.outcome is GateOutcome.ALLOW
    assert read_only.reason_code == "READ_ONLY"

    unknown = gate.evaluate(episode.id, "deploy_widget", {"target": "production"})
    assert unknown.outcome is GateOutcome.BLOCK
    assert unknown.reason_code == "UNKNOWN_EFFECTFUL_TOOL"

    disabled_config = {**config, "gating": {**config["gating"], "enabled": False}}
    disabled = ActionGate(store, disabled_config, policies).evaluate(
        episode.id, "write_file", {"path": "result.txt"}
    )
    assert disabled.outcome is GateOutcome.ALLOW
    assert disabled.reason_code == "GATE_DISABLED"


def test_core_structured_model_client_records_success_and_stable_failure(tmp_path: Path) -> None:
    store, episode = _store_with_episode(tmp_path)
    config = _data("defaults.yaml")
    success = StructuredModelResult(
        1,
        {"supported": True},
        "fake-provider",
        "fake-model",
        input_tokens=4,
        output_tokens=2,
    )
    dependencies = replace(
        deterministic_dependencies(),
        structured_model=FakeStructuredModel([success]),
    )
    client = HostLlmClient(dependencies.structured_model, store, config, dependencies)

    result = client.complete_structured(
        episode_id=episode.id,
        purpose="evaluation.entailment",
        instructions="Classify support.",
        text="The service is healthy.",
        schema={"type": "object"},
        schema_name="support",
        max_tokens=16,
        validator=lambda value: value,
    )
    assert result.parsed == {"supported": True}
    assert result.provider == "fake-provider"
    assert len(result.event_ids) == 2

    with pytest.raises(LlmComponentError, match=r"evaluation\.failure failed"):
        client.complete_structured(
            episode_id=episode.id,
            purpose="evaluation.failure",
            instructions="Fail deterministically.",
            text="input",
            schema={"type": "object"},
            schema_name="failure",
            max_tokens=8,
            validator=lambda value: value,
        )

    with pytest.raises(LlmComponentError, match="episode does not exist"):
        client.complete_structured(
            episode_id="missing",
            purpose="evaluation.missing",
            instructions="No-op.",
            text="input",
            schema={},
            schema_name="missing",
            max_tokens=1,
            validator=lambda value: value,
        )
