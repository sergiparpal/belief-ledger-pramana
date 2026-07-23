from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from belief_ledger_core.dependencies import StructuredModelProviderError, StructuredModelRequest
from belief_ledger_core.gate.classify import ActionPolicyRegistry
from belief_ledger_core.gate.decision import ActionGate
from belief_ledger_core.models import (
    Belief,
    CompatibilityMode,
    Episode,
    GateOutcome,
    Integrity,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    Stakes,
    Status,
    VerificationMethod,
)
from belief_ledger_core.store import LedgerStore
from belief_ledger_core.verification.scheduler import VerificationScheduler

from belief_ledger_pramana.config import packaged_yaml
from belief_ledger_pramana.hermes.model_port import HermesStructuredModelPort


def _episode() -> Episode:
    now = datetime.now(UTC)
    return Episode(
        "episode-hardening",
        "session:hardening",
        "session",
        "",
        "test",
        "none",
        Stakes.MED,
        0,
        now,
        now,
        CompatibilityMode.FULL,
    )


def _policy(*, preconditions: list[str], base_stakes: str = "high") -> ActionPolicyRegistry:
    return ActionPolicyRegistry(
        {
            "schema_version": 1,
            "rules": [
                {
                    "id": "custom",
                    "exact": ["custom_action"],
                    "base_stakes": base_stakes,
                    "effectful": True,
                    "minimum_priority": "untrusted",
                    "allow_human_approval": True,
                    "target_fields": ["target"],
                    "preconditions": preconditions,
                }
            ],
        }
    )


def test_core_action_gate_covers_fail_closed_and_operator_modes(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    config = copy.deepcopy(packaged_yaml("defaults.yaml"))
    packaged_policies = ActionPolicyRegistry(packaged_yaml("action-policies.yaml"))
    gate = ActionGate(store, config, packaged_policies)

    assert gate.evaluate("missing", "read_file", {"path": "x"}).reason_code == "EPISODE_UNAVAILABLE"
    episode = _episode()
    store.create_episode(episode)
    assert gate.evaluate(episode.id, "mystery_tool", {}).reason_code == "UNKNOWN_EFFECTFUL_TOOL"
    assert gate.evaluate(episode.id, "read_file", {"path": "x"}).reason_code == "READ_ONLY"
    blocked = gate.evaluate(episode.id, "write_file", {"path": "/tmp/x"})
    assert blocked.reason_code == "MISSING_PRECONDITION"

    disabled = copy.deepcopy(config)
    disabled["gating"]["enabled"] = False
    assert (
        ActionGate(store, disabled, packaged_policies)
        .evaluate(episode.id, "write_file", {"path": "/tmp/x"})
        .reason_code
        == "GATE_DISABLED"
    )


def test_core_action_gate_allows_complete_rules_and_requests_bound_approval(
    tmp_path: Path,
) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    episode = _episode()
    store.create_episode(episode)
    config = copy.deepcopy(packaged_yaml("defaults.yaml"))

    allowed = ActionGate(store, config, _policy(preconditions=[])).evaluate(
        episode.id, "custom_action", {"target": "production"}
    )
    assert allowed.reason_code == "PRECONDITIONS_SATISFIED"
    approval = ActionGate(
        store,
        config,
        _policy(preconditions=["explicit_user_confirmation"]),
    ).evaluate(episode.id, "custom_action", {"target": "production"})
    assert approval.outcome is GateOutcome.APPROVE
    assert approval.rule_key == "belief-ledger:custom"

    critical = ActionGate(
        store,
        config,
        _policy(preconditions=[], base_stakes="critical"),
    ).evaluate(episode.id, "custom_action", {"target": "production"})
    assert critical.outcome is GateOutcome.APPROVE


def _source(identifier: str, episode_id: str, root: str) -> Source:
    return Source(
        identifier,
        episode_id,
        SourceKind.WEB,
        Integrity.SEMI,
        identifier,
        root,
        {"general": 0.8},
    )


def _belief(identifier: str, source: Source, *, status: Status = Status.IN) -> Belief:
    return Belief(
        identifier,
        source.episode_id,
        "Atlas is healthy",
        "atlas is healthy",
        Pramana.SHABDA,
        source.id,
        (),
        (),
        {},
        Perishability.FAST,
        datetime.now(UTC),
        Stakes.MED,
        status,
        status,
    )


def test_core_verification_scheduler_deduplicates_counts_and_completes(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    episode = _episode()
    store.create_episode(episode)
    scheduler = VerificationScheduler(store, {"ingestion": {"near_duplicate_threshold": 0.9}})
    primary_source = _source("source-primary", episode.id, "web:primary")
    primary = _belief("belief-primary", primary_source)
    store.append_record(
        episode.id,
        kind="SOURCE_REGISTERED",
        aggregate_type="source",
        aggregate_id=primary_source.id,
        record=primary_source,
    )
    store.append_record(
        episode.id,
        kind="BELIEF_ADMITTED",
        aggregate_type="belief",
        aggregate_id=primary.id,
        record=primary,
    )

    created = scheduler.request(
        episode.id,
        primary.id,
        VerificationMethod.CROSS_SOURCE,
        k_required=100,
        budget=-1,
    )
    assert created.created and created.task.k_required == 20 and created.task.budget == 0
    repeated = scheduler.request(episode.id, primary.id, VerificationMethod.CROSS_SOURCE)
    assert not repeated.created and repeated.task.id == created.task.id
    with pytest.raises(ValueError, match="invalid verification result"):
        scheduler.complete(created.task, "maybe")
    assert scheduler.complete(created.task, "confirmed")
    assert scheduler.complete(created.task, "confirmed") == ()

    other_source = _source("source-other", episode.id, "web:other")
    same_root = _source("source-mirror", episode.id, "web:primary")
    candidates = [
        primary,
        _belief("belief-out", other_source, status=Status.OUT),
        _belief("belief-quarantined", other_source, status=Status.QUARANTINED),
        _belief("belief-independent", other_source),
        _belief("belief-same-root", same_root),
    ]
    sources = {item.id: item for item in (primary_source, other_source, same_root)}
    assert scheduler.passive_cross_source_count(primary, candidates, sources) == 1


class _Facade:
    def __init__(self, result: object | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def complete_structured(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_hermes_model_port_translates_results_usage_and_provider_errors() -> None:
    usage = SimpleNamespace(input_tokens=4, output_tokens=2, cost_usd=0.25)
    facade = _Facade(
        SimpleNamespace(parsed={"ok": True}, provider="host", model="model", usage=usage)
    )
    port = HermesStructuredModelPort(lambda: facade)
    request = StructuredModelRequest(
        1,
        "belief-ledger.classify",
        "Classify",
        "payload",
        {"type": "object"},
        32,
        3.0,
    )
    result = port.complete(request)
    assert result.parsed == {"ok": True}
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (4, 2, 0.25)
    assert facade.calls[0]["schema_name"] == "belief-ledger_classify"

    no_usage = HermesStructuredModelPort(
        lambda: _Facade(SimpleNamespace(parsed=None, provider=None, model=None, usage=None))
    ).complete(request)
    assert no_usage.input_tokens == no_usage.output_tokens == 0
    assert no_usage.cost_usd is None

    with pytest.raises(StructuredModelProviderError, match="RuntimeError"):
        HermesStructuredModelPort(lambda: _Facade(RuntimeError("offline"))).complete(request)
