from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime

import yaml

from belief_ledger_pramana.config import packaged_yaml
from belief_ledger_pramana.engine.defeat import _defeat_cycle_nodes, relabel
from belief_ledger_pramana.gate.classify import ActionPolicyRegistry
from belief_ledger_pramana.gate.decision import ActionGate
from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.models import (
    Belief,
    DefeatEdge,
    DefeatKind,
    EvidenceRef,
    GateOutcome,
    IngestionSupport,
    Integrity,
    Justification,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    Status,
)


def _source(episode_id: str) -> Source:
    return Source(
        new_id("source"),
        episode_id,
        SourceKind.TOOL,
        Integrity.TRUSTED,
        "observer",
        f"tool:{episode_id}",
        {"general": 1.0},
        SourceStats(),
    )


def _basic(episode_id: str, source: Source, content: str) -> Belief:
    return Belief(
        new_id("belief"),
        episode_id,
        content,
        content.casefold(),
        Pramana.PRATYAKSHA,
        source.id,
        (EvidenceRef(new_id("evidence")),),
        (),
        {},
        Perishability.STABLE,
        datetime(2026, 7, 11, tzinfo=UTC),
        Stakes.LOW,
        Status.IN,
        Status.IN,
    )


def _support(belief: Belief) -> IngestionSupport:
    return IngestionSupport(
        new_id("support"), belief.episode_id, belief.id, belief.evidence[0].evidence_id, {}
    )


def test_mutual_undercuts_terminate_as_samsaya(runtime) -> None:
    episode_id = new_id("episode")
    source = _source(episode_id)
    left = _basic(episode_id, source, "Left probe is active")
    right = _basic(episode_id, source, "Right probe is active")
    left_support = _support(left)
    right_support = _support(right)
    defeats = (
        DefeatEdge(
            new_id("defeat"),
            episode_id,
            left.id,
            right_support.id,
            DefeatKind.UNDERCUT,
            "left invalidates right observation",
        ),
        DefeatEdge(
            new_id("defeat"),
            episode_id,
            right.id,
            left_support.id,
            DefeatKind.UNDERCUT,
            "right invalidates left observation",
        ),
    )
    outcome = relabel(
        {left.id: left, right.id: right},
        (),
        (left_support, right_support),
        defeats,
        {source.id: source},
        runtime.config.data,
    )
    assert outcome.oscillation
    assert outcome.iterations <= 3
    assert all(status is Status.PENDING for status in outcome.statuses.values())
    assert not any(outcome.active_edges.values())


def test_iteration_ceiling_never_leaves_unsupported_conclusion_in(runtime) -> None:
    episode_id = new_id("episode")
    source = _source(episode_id)
    root = _basic(episode_id, source, "Root probe is active")
    conclusion_id = new_id("belief")
    justification = Justification(
        new_id("justification"), conclusion_id, (root.id,), "root supports conclusion"
    )
    conclusion = Belief(
        conclusion_id,
        episode_id,
        "Conclusion is active",
        "conclusion is active",
        Pramana.ANUMANA,
        source.id,
        (),
        (justification,),
        {},
        Perishability.STABLE,
        root.observed_at,
        Stakes.LOW,
        Status.IN,
        Status.IN,
    )
    inactive = replace(_support(root), active=False)
    config = copy.deepcopy(runtime.config.data)
    config["engine"]["max_relabel_iterations"] = 1
    outcome = relabel(
        {root.id: root, conclusion.id: conclusion},
        (justification,),
        (inactive,),
        (),
        {source.id: source},
        config,
    )
    assert outcome.oscillation
    assert outcome.statuses[root.id] is Status.OUT
    assert outcome.statuses[conclusion.id] is Status.OUT


def test_quarantine_missing_edges_and_defeat_cycle_helpers(runtime) -> None:
    episode_id = new_id("episode")
    source = _source(episode_id)
    left = _basic(episode_id, source, "Left is active")
    right = replace(
        _basic(episode_id, source, "Right is active"),
        status=Status.QUARANTINED,
        admission_status=Status.QUARANTINED,
    )
    left_support = _support(left)
    right_support = _support(right)
    missing = DefeatEdge(
        new_id("defeat"), episode_id, "missing", right.id, DefeatKind.REBUT, "missing"
    )
    outcome = relabel(
        {left.id: left, right.id: right},
        (),
        (left_support, right_support),
        (missing,),
        {source.id: source},
        runtime.config.data,
    )
    assert outcome.statuses[left.id] is Status.IN
    assert outcome.statuses[right.id] is Status.QUARANTINED
    assert outcome.active_edges[missing.id] is False

    edges = (
        DefeatEdge(new_id("defeat"), episode_id, left.id, right.id, DefeatKind.REBUT, "a"),
        DefeatEdge(new_id("defeat"), episode_id, right.id, left.id, DefeatKind.REBUT, "b"),
    )
    assert _defeat_cycle_nodes(edges) == {left.id, right.id}


def test_gate_disabled_episode_missing_approval_and_allow_paths(runtime) -> None:
    service = runtime.begin_turn(
        session_id="gate-edge",
        turn_id="gate-edge-turn",
        user_message="Inspect before acting.",
    )
    policies = ActionPolicyRegistry(packaged_yaml("action-policies.yaml"))
    missing = ActionGate(service.store, service.config, policies).evaluate(
        "ep_missing", "write_file", {"path": "x"}
    )
    assert missing.outcome is GateOutcome.BLOCK
    assert missing.reason_code == "EPISODE_UNAVAILABLE"

    disabled_config = copy.deepcopy(service.config)
    disabled_config["gating"]["enabled"] = False
    disabled = ActionGate(service.store, disabled_config, policies).evaluate(
        service.episode_id, "write_file", {"path": "x"}
    )
    assert disabled.outcome is GateOutcome.ALLOW
    assert disabled.reason_code == "GATE_DISABLED"

    delegated = service.gate_action("delegate_task", {"goal": "read-only research"})
    assert delegated.outcome is GateOutcome.ALLOW
    assert delegated.reason_code == "PRECONDITIONS_SATISFIED"

    # This test exercises approval mechanics; production policy normally
    # requires cross-source verification for world claims at high stakes.
    updated_config = copy.deepcopy(runtime.config.data)
    updated_config["trust"]["matrix"]["user_world"]["high"] = {
        "mode": "svatah",
        "k": 0,
        "method": None,
    }
    if runtime.config.source is None:
        raise RuntimeError("test runtime configuration source is unavailable")
    runtime.config.source.write_text(
        yaml.safe_dump(updated_config, sort_keys=False),
        encoding="utf-8",
    )
    service = runtime.begin_turn(
        session_id="gate-edge",
        turn_id="gate-edge-configured-turn",
        user_message="Resource bob is the intended target.",
    )
    service.ingest_user_message(
        "Resource bob is the intended target.",
        session_id="gate-edge",
        turn_id="gate-edge-configured-turn",
        sender_id="user",
    )
    approval = service.gate_action("send_email", {"recipient": "bob"})
    assert approval.outcome is GateOutcome.APPROVE
    assert approval.reason_code == "HUMAN_CONFIRMATION_REQUIRED"
    assert approval.rule_key

    service.ingest_user_message(
        "I confirm send email to bob.",
        session_id="gate-edge",
        turn_id="gate-edge-configured-turn",
        sender_id="user",
    )
    still_bound_to_host_approval = service.gate_action("send_email", {"recipient": "bob"})
    assert still_bound_to_host_approval.outcome is GateOutcome.APPROVE

    service.ingest_user_message(
        "Resource alice is the intended target.",
        session_id="gate-edge",
        turn_id="gate-edge-turn",
        sender_id="user",
    )
    service.ingest_user_message(
        "I do not confirm send email to alice.",
        session_id="gate-edge",
        turn_id="gate-edge-turn",
        sender_id="user",
    )
    denied_confirmation = service.gate_action("send_email", {"recipient": "alice"})
    assert denied_confirmation.outcome is GateOutcome.APPROVE
    assert denied_confirmation.reason_code == "HUMAN_CONFIRMATION_REQUIRED"

    elevated = runtime.begin_turn(
        session_id="gate-elevation",
        turn_id="gate-elevation-turn",
        user_message="Resource alice is the intended target.",
        sender_id="user",
    )
    elevated.ingest_user_message(
        "Resource alice is the intended target.",
        session_id="gate-elevation",
        turn_id="gate-elevation-turn",
        sender_id="user",
    )
    denied = elevated.gate_action("send_email", {"recipient": "alice"})
    assert denied.outcome is not GateOutcome.ALLOW
