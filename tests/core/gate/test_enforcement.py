from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from belief_ledger_core.dependencies import FixedClock, deterministic_dependencies
from belief_ledger_core.enforcement import (
    ActionBinding,
    ApprovalBinding,
    EnforcementStore,
)


def _action(receipt: str | None = None) -> ActionBinding:
    return ActionBinding(
        1,
        "episode-1",
        "turn-1",
        "deployments",
        "deploy",
        "args-digest",
        "production",
        "deploy-production",
        "policy-v1",
        1,
        "policy-content-v1",
        "config-content-v1",
        "critical",
        ("belief-health",),
        ("conflict-production",),
        receipt,
    )


def _approval(**changes) -> ApprovalBinding:
    values = {
        "schema_version": 1,
        "episode_id": "episode-1",
        "turn_id": "turn-1",
        "namespace": "deployments",
        "tool_name": "deploy",
        "arguments_hash": "args-digest",
        "target": "production",
        "policy_id": "deploy-production",
        "policy_revision": "policy-v1",
        "scope": "exact_action",
    }
    values.update(changes)
    return ApprovalBinding(**values)


def _store(tmp_path: Path):
    dependencies = deterministic_dependencies()
    return EnforcementStore(tmp_path / "authorization.sqlite3", dependencies), dependencies


def test_action_token_is_digest_persisted_exact_and_single_use(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    receipt = store.issue_approval(_approval(), ttl_seconds=60)
    assert receipt is not None
    decision = store.issue_action(_action(receipt.digest), ttl_seconds=30)
    assert decision.token != decision.token_digest
    database_material = store.database.read_bytes()
    assert decision.token.encode() not in database_material
    assert all(
        decision.token.encode() not in path.read_bytes()
        for path in store.database.parent.glob(f"{store.database.name}*")
    )
    connection = sqlite3.connect(store.database)
    try:
        persisted = connection.execute(
            "SELECT token_digest,binding_json FROM action_decisions"
        ).fetchone()
    finally:
        connection.close()
    assert persisted is not None and persisted[0] == decision.token_digest
    assert decision.token not in str(persisted)
    with pytest.raises(ValueError, match="APPROVAL_CONSUMED"):
        store.issue_action(_action(receipt.digest), ttl_seconds=30)

    mismatch = store.consume_action(
        decision.token,
        replace(decision.binding, target="staging"),
    )
    assert mismatch.reason_code == "TARGET_MISMATCH"
    consumed = store.consume_action(decision.token, decision.binding)
    assert consumed.consumed is True
    assert store.action_state(decision.token_digest) == "consumed"
    reused = store.consume_action(decision.token, decision.binding)
    assert reused.reason_code == "TOKEN_CONSUMED"
    serialized = str(store.events())
    assert decision.token not in serialized
    assert "ACTION_DECISION_CONSUMED" in serialized
    assert "APPROVAL_RECEIPT_CONSUMED" in serialized
    assert store.rebuild()
    assert store.action_state(decision.token_digest) == "consumed"


def test_state_projections_allow_only_one_way_transitions(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    decision = store.issue_action(_action(), ttl_seconds=30)
    assert store.consume_action(decision.token, decision.binding).consumed
    connection = sqlite3.connect(store.database)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="invalid action state transition"):
            connection.execute(
                "UPDATE action_decisions SET state='issued' WHERE token_digest=?",
                (decision.token_digest,),
            )
        version = connection.execute(
            "SELECT MAX(version) FROM enforcement_schema_migrations"
        ).fetchone()
    finally:
        connection.close()
    assert version == (1,)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("episode_id", "other", "EPISODE_MISMATCH"),
        ("turn_id", "other", "TURN_MISMATCH"),
        ("namespace", "other", "TOOL_NAMESPACE_MISMATCH"),
        ("tool_name", "other", "TOOL_NAME_MISMATCH"),
        ("arguments_hash", "other", "ARGUMENTS_MISMATCH"),
        ("policy_revision", "other", "POLICY_REVISION_MISMATCH"),
        ("policy_content_digest", "other", "POLICY_CONTENT_DRIFT"),
        ("config_content_digest", "other", "CONFIG_CONTENT_DRIFT"),
        ("supporting_belief_ids", ("other",), "SUPPORT_BINDING_MISMATCH"),
        ("blocking_conflict_ids", ("other",), "CONFLICT_BINDING_MISMATCH"),
    ],
)
def test_action_binding_substitutions_fail_closed(
    tmp_path: Path, field: str, value: object, reason: str
) -> None:
    store, _ = _store(tmp_path)
    decision = store.issue_action(_action(), ttl_seconds=30)
    result = store.consume_action(decision.token, replace(decision.binding, **{field: value}))
    assert result.reason_code == reason
    assert store.action_state(decision.token_digest) == "issued"


def test_expiry_unknown_token_support_retraction_and_proactive_revoke(tmp_path: Path) -> None:
    store, dependencies = _store(tmp_path)
    decision = store.issue_action(_action(), ttl_seconds=10)
    clock = cast(FixedClock, dependencies.clock)
    clock.advance(10)
    expired = store.consume_action(decision.token, decision.binding)
    assert expired.reason_code == "TOKEN_EXPIRED"
    assert store.action_state(decision.token_digest) == "expired"
    assert store.consume_action("unknown", _action()).reason_code == "TOKEN_NOT_FOUND"

    lazy = store.issue_action(_action(), ttl_seconds=10)
    rejected = store.consume_action(
        lazy.token, lazy.binding, support_is_active=lambda identifiers: False
    )
    assert rejected.reason_code == "SUPPORT_RETRACTED"
    proactive = store.issue_action(_action(), ttl_seconds=10)
    assert store.revoke_for_support("belief-health") == 1
    assert store.action_state(proactive.token_digest) == "revoked"
    assert store.revoke_for_support("unrelated") == 0

    conflict = store.issue_action(_action(), ttl_seconds=10)
    blocked = store.consume_action(
        conflict.token,
        conflict.binding,
        conflicts_are_closed=lambda identifiers: False,
    )
    assert blocked.reason_code == "OPEN_CONFLICT"
    assert store.action_state(conflict.token_digest) == "revoked"


def test_approval_denial_mismatch_expiry_and_revocation(tmp_path: Path) -> None:
    store, dependencies = _store(tmp_path)
    assert store.issue_approval(_approval(), ttl_seconds=10, approved=False) is None
    with pytest.raises(ValueError, match="positive"):
        store.issue_approval(_approval(), ttl_seconds=0)
    with pytest.raises(ValueError, match="positive"):
        store.issue_action(_action(), ttl_seconds=0)

    wrong = store.issue_approval(_approval(target="staging"), ttl_seconds=10)
    assert wrong is not None
    with pytest.raises(ValueError, match="APPROVAL_BINDING_MISMATCH"):
        store.issue_action(_action(wrong.digest), ttl_seconds=10)

    expired = store.issue_approval(_approval(), ttl_seconds=1)
    assert expired is not None
    cast(FixedClock, dependencies.clock).advance(1)
    with pytest.raises(ValueError, match="APPROVAL_EXPIRED"):
        store.issue_action(_action(expired.digest), ttl_seconds=10)

    active = store.issue_approval(_approval(), ttl_seconds=10)
    assert active is not None
    assert store.revoke_approval(active.digest)
    assert not store.revoke_approval(active.digest)
    with pytest.raises(ValueError, match="APPROVAL_REVOKED"):
        store.issue_action(_action(active.digest), ttl_seconds=10)


def test_concurrent_consumers_cannot_both_succeed(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    decision = store.issue_action(_action(), ttl_seconds=30)

    def consume(_: int):
        return store.consume_action(decision.token, decision.binding)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(consume, range(16)))
    assert sum(result.consumed for result in results) == 1
    assert {result.reason_code for result in results} == {"CONSUMED", "TOKEN_CONSUMED"}
