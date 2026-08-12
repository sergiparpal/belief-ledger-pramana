"""Attribution and divergence detection for the model component (ADR 0012).

The point of these tests is the one the plan names: a port that returns different structured
results for byte-identical input must be *detectable*. `temperature=0.0` reduces non-determinism;
nothing available to this process removes it, so the deliverable is the audit.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from belief_ledger_core.dependencies import (
    SamplingPolicy,
    StructuredModelRequest,
    StructuredModelResult,
    deterministic_dependencies,
)
from belief_ledger_core.llm.attribution import (
    call_input_hash,
    call_output_hash,
    prompt_hash,
    sampling_policy,
)
from belief_ledger_core.llm.client import HostLlmClient
from belief_ledger_core.llm.divergence import divergence_report, recorded_calls
from belief_ledger_core.models import CompatibilityMode, Episode, Stakes
from belief_ledger_core.store import LedgerStore

CALL = {
    "purpose": "evaluation.entailment",
    "instructions": "Classify support.",
    "text": "The service is healthy.",
    "schema": {"type": "object"},
    "schema_name": "support",
    "max_tokens": 16,
}


def _config() -> dict[str, Any]:
    value = yaml.safe_load(
        files("belief_ledger_core.data").joinpath("defaults.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _store_with_episode(tmp_path: Path) -> tuple[LedgerStore, Episode]:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    now = datetime(2026, 7, 23, tzinfo=UTC)
    episode = Episode(
        "episode-divergence",
        "session:divergence",
        "divergence",
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


class ScriptedPort:
    """A port that answers byte-identical requests however it is told to."""

    def __init__(self, parsed_values: list[Any]) -> None:
        self._parsed = list(parsed_values)
        self.requests: list[StructuredModelRequest] = []

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        self.requests.append(request)
        parsed = self._parsed.pop(0)
        return StructuredModelResult(1, parsed, "fake-provider", "fake-model", 4, 2)


def _client(store: LedgerStore, port: ScriptedPort, config: dict[str, Any]) -> HostLlmClient:
    dependencies = replace(deterministic_dependencies(), structured_model=port)
    return HostLlmClient(port, store, config, dependencies)


def _call(client: HostLlmClient, episode_id: str) -> None:
    client.complete_structured(episode_id=episode_id, validator=lambda value: value, **CALL)


# --- sampling policy ---------------------------------------------------------------------------


def test_the_default_sampling_policy_is_temperature_zero() -> None:
    assert SamplingPolicy().temperature == 0.0
    assert sampling_policy().temperature == 0.0
    assert sampling_policy(0.7).temperature == 0.7


@pytest.mark.parametrize("value", [-0.1, 2.1, "hot", True])
def test_an_invalid_sampling_temperature_is_refused_rather_than_clamped(value: Any) -> None:
    """A silently corrected setting would no longer describe what was asked of the provider."""
    with pytest.raises(ValueError):
        SamplingPolicy(value)


def test_the_packaged_default_configures_temperature_zero() -> None:
    assert _config()["verification"]["sampling_temperature"] == 0.0


def test_the_configured_policy_reaches_the_port_and_the_record(tmp_path: Path) -> None:
    store, episode = _store_with_episode(tmp_path)
    config = _config()
    config["verification"] = {**config["verification"], "sampling_temperature": 0.25}
    port = ScriptedPort([{"supported": True}])

    _call(_client(store, port, config), episode.id)

    assert port.requests[0].sampling.temperature == 0.25
    call = recorded_calls(store.events(episode.id))[0]
    assert call.sampling == {"temperature": 0.25}


# --- attribution -------------------------------------------------------------------------------


def test_every_call_records_prompt_input_and_output_digests(tmp_path: Path) -> None:
    store, episode = _store_with_episode(tmp_path)
    port = ScriptedPort([{"supported": True}])

    _call(_client(store, port, _config()), episode.id)

    call = recorded_calls(store.events(episode.id))[0]
    assert call.prompt_hash == prompt_hash(CALL["instructions"])
    assert call.input_hash == call_input_hash(
        instructions=CALL["instructions"],
        text=CALL["text"],
        schema_name=CALL["schema_name"],
        max_tokens=CALL["max_tokens"],
    )
    assert call.output_hash == call_output_hash({"supported": True})
    assert call.model == "fake-model"
    assert call.outcome == "success"


def test_the_input_hash_separates_calls_that_differ_only_in_schema_or_budget() -> None:
    """Two calls with the same text but a different request are not the same input."""
    base = call_input_hash(instructions="i", text="t", schema_name="a", max_tokens=8)

    assert base != call_input_hash(instructions="i", text="t", schema_name="b", max_tokens=8)
    assert base != call_input_hash(instructions="i", text="t", schema_name="a", max_tokens=9)
    assert base != call_input_hash(instructions="j", text="t", schema_name="a", max_tokens=8)
    assert base == call_input_hash(instructions="i", text="t", schema_name="a", max_tokens=8)


def test_the_input_hash_is_computed_over_redacted_text() -> None:
    """The digest must never commit to a credential."""
    with_secret = call_input_hash(
        instructions="i",
        text="Authorization: Bearer secret-value",
        schema_name="a",
        max_tokens=8,
    )
    with_other_secret = call_input_hash(
        instructions="i",
        text="Authorization: Bearer different-value",
        schema_name="a",
        max_tokens=8,
    )

    assert with_secret == with_other_secret, "redaction must erase the credential before hashing"


def test_a_failed_call_records_attribution_with_no_output(tmp_path: Path) -> None:
    store, episode = _store_with_episode(tmp_path)
    port = ScriptedPort([{"supported": True}])
    client = _client(store, port, _config())

    with pytest.raises(Exception):  # noqa: B017 - the wrapper type is asserted elsewhere
        client.complete_structured(
            episode_id=episode.id,
            validator=lambda value: (_ for _ in ()).throw(ValueError("rejected")),
            **CALL,
        )

    call = recorded_calls(store.events(episode.id))[0]
    assert call.output_hash is None
    assert call.outcome == "ValueError"


# --- divergence --------------------------------------------------------------------------------


def test_two_different_outputs_for_one_input_are_reported_as_one_group(tmp_path: Path) -> None:
    """The acceptance test: byte-identical input, different structured results."""
    store, episode = _store_with_episode(tmp_path)
    port = ScriptedPort([{"supported": True}, {"supported": False}])
    client = _client(store, port, _config())

    _call(client, episode.id)
    _call(client, episode.id)

    assert port.requests[0] == port.requests[1], "the two requests must be byte-identical"
    calls = recorded_calls(store.events(episode.id))
    assert calls[0].input_hash == calls[1].input_hash
    assert calls[0].output_hash != calls[1].output_hash

    report = divergence_report(store.events(episode.id))
    assert report["recorded_calls"] == 2
    assert report["distinct_inputs"] == 1
    assert report["divergent_groups"] == 1
    group = report["groups"][0]
    assert group["distinct_outputs"] == 2
    assert group["purpose"] == "evaluation.entailment"
    assert [item["event_id"] for item in group["calls"]] == [
        calls[0].event_id,
        calls[1].event_id,
    ]


def test_identical_results_twice_report_nothing(tmp_path: Path) -> None:
    store, episode = _store_with_episode(tmp_path)
    port = ScriptedPort([{"supported": True}, {"supported": True}])
    client = _client(store, port, _config())

    _call(client, episode.id)
    _call(client, episode.id)

    report = divergence_report(store.events(episode.id))
    assert report["recorded_calls"] == 2
    assert report["divergent_groups"] == 0
    assert report["groups"] == []


def test_key_order_alone_is_not_a_divergence(tmp_path: Path) -> None:
    """Canonical JSON is what makes the output digest comparable."""
    store, episode = _store_with_episode(tmp_path)
    port = ScriptedPort([{"a": 1, "b": 2}, {"b": 2, "a": 1}])
    client = _client(store, port, _config())

    _call(client, episode.id)
    _call(client, episode.id)

    assert divergence_report(store.events(episode.id))["divergent_groups"] == 0


def test_a_failed_call_is_not_counted_as_a_divergent_answer(tmp_path: Path) -> None:
    """An error is the absence of an answer, not a second one."""
    store, episode = _store_with_episode(tmp_path)
    port = ScriptedPort([{"supported": True}, {"supported": True}])
    client = _client(store, port, _config())

    _call(client, episode.id)
    with pytest.raises(Exception):  # noqa: B017
        client.complete_structured(
            episode_id=episode.id,
            validator=lambda value: (_ for _ in ()).throw(ValueError("rejected")),
            **CALL,
        )

    report = divergence_report(store.events(episode.id))
    assert report["recorded_calls"] == 2
    assert report["divergent_groups"] == 0


def test_a_different_prompt_is_a_different_group_not_a_divergence(tmp_path: Path) -> None:
    store, episode = _store_with_episode(tmp_path)
    port = ScriptedPort([{"supported": True}, {"supported": False}])
    client = _client(store, port, _config())

    _call(client, episode.id)
    client.complete_structured(
        episode_id=episode.id,
        validator=lambda value: value,
        **{**CALL, "instructions": "A different instruction entirely."},
    )

    report = divergence_report(store.events(episode.id))
    assert report["distinct_inputs"] == 2
    assert report["divergent_groups"] == 0
