"""Regressions for defects found in the 2026-08-05 exhaustive codebase review.

Each test here fails against the code as it stood before that review. They are grouped by the
defect they pin rather than by module, so a future change that reintroduces one is reported
against the behaviour that was actually wrong.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml
from belief_ledger_core.contracts import (
    EnforcementProfile,
    EpisodeContext,
    HostCapabilities,
    ToolInvocation,
    negotiate_profile,
)
from belief_ledger_core.dependencies import deterministic_dependencies
from belief_ledger_core.enforcement import ActionBinding, EnforcementStore
from belief_ledger_core.events import isoformat_utc, parse_datetime, utc_now
from belief_ledger_core.gate.classify import ActionPolicyRegistry
from belief_ledger_core.gate.decision import ActionGate, arguments_digest
from belief_ledger_core.migrations import LATEST_SCHEMA_VERSION
from belief_ledger_core.models import CompatibilityMode, Episode, GateOutcome, Stakes
from belief_ledger_core.store import EventDraft, LedgerStore

from belief_ledger_pramana.config import (
    PLUGIN_STATE_DIR,
    ConfigError,
    _directories_within,
    load_config,
    packaged_yaml,
    state_paths,
)

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX private-state permissions are asserted by the loader"
)


def _episode(store: LedgerStore, key: str = "k") -> Episode:
    now = utc_now()
    episode = Episode(
        f"ep_{'a' * 20}{key}",
        key,
        "session",
        "task",
        "platform",
        "model",
        Stakes.MED,
        1,
        now,
        now,
        CompatibilityMode.FULL,
    )
    store.create_episode(episode)
    return episode


def _private_state_root(tmp_path: Path) -> Path:
    root = tmp_path / PLUGIN_STATE_DIR
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


# --- Configuration: extension paths were validated at one location and read from another ---


def test_relative_extension_path_is_resolved_against_the_state_root_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative policy_files entry must resolve inside the state root.

    The resolution used to be written into a defensive copy and discarded, so the file that
    was permission-checked was not the file later loaded as action policy.
    """

    root = _private_state_root(tmp_path)
    extension = root / "extra-policies.yaml"
    extension.write_text(yaml.safe_dump({"schema_version": 1, "rules": []}), encoding="utf-8")
    extension.chmod(0o600)
    config_path = root / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"gating": {"policy_files": ["extra-policies.yaml"]}}), encoding="utf-8"
    )
    config_path.chmod(0o600)

    # A working directory that is deliberately not the state root: a relative path read from
    # here would resolve to a different file, or to nothing at all.
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    snapshot, _ = load_config(hermes_home=tmp_path)

    resolved = list(snapshot.data["gating"]["policy_files"])
    assert resolved == [str(extension)]
    assert Path(resolved[0]).is_absolute()
    assert snapshot.extension_digests and snapshot.extension_digests[0][0] == str(extension)


def test_extension_path_outside_the_state_root_is_rejected(tmp_path: Path) -> None:
    root = _private_state_root(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text(yaml.safe_dump({"schema_version": 1, "rules": []}), encoding="utf-8")
    outside.chmod(0o600)
    config_path = root / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"gating": {"policy_files": [str(outside)]}}), encoding="utf-8"
    )
    config_path.chmod(0o600)

    with pytest.raises(ConfigError, match="inside the plugin state directory"):
        load_config(hermes_home=tmp_path)


# --- Configuration: a path outside the root walked to the filesystem root forever ---


def test_directory_walk_terminates_for_a_target_outside_the_root(tmp_path: Path) -> None:
    root = _private_state_root(tmp_path)
    with pytest.raises(ConfigError, match="not inside the plugin state directory"):
        _directories_within(root, tmp_path.parent)


def test_database_resolving_to_the_state_root_itself_is_rejected(tmp_path: Path) -> None:
    # "." resolves to the root, whose parent is outside the tree; directory creation then had
    # no well-founded stopping point.
    with pytest.raises(ConfigError, match="must resolve to a file"):
        state_paths(tmp_path, ".")


# --- Projections: two timestamp encodings in one text-ordered column ---


def test_every_episode_timestamp_uses_one_sortable_encoding(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    episode = _episode(store)
    store.append_events(
        episode.id,
        [EventDraft("EPISODE_STAKES_CHANGED", "episode", episode.id, {"from": "med", "to": "high"})],
    )

    with store.connect() as connection:
        stored = str(connection.execute("SELECT updated_at FROM episodes").fetchone()[0])

    assert stored.endswith("Z"), "a '+00:00' suffix sorts before 'Z' and reverses ORDER BY"
    assert stored == isoformat_utc(parse_datetime(stored))


def _ingest_repeatedly(runtime: object, session: str, turns: tuple[str, ...]) -> object:
    """Ingest the same claim across several turns, driving the duplicate-refresh path."""

    service = runtime.begin_turn(session_id=session, turn_id=turns[0], user_message="Observe")  # type: ignore[attr-defined]
    for turn in turns:
        service.ingest_user_message(
            "Atlas is healthy.", session_id=session, turn_id=turn, sender_id="operator"
        )
    return service


def test_a_refreshed_observation_keeps_beliefs_orderable_by_observed_at(runtime: object) -> None:
    service = _ingest_repeatedly(runtime, "dupes", ("one", "two"))

    assert runtime.store is not None  # type: ignore[attr-defined]
    with runtime.store.connect() as connection:  # type: ignore[attr-defined]
        observed = [
            str(row[0])
            for row in connection.execute(
                "SELECT observed_at FROM beliefs WHERE episode_id=?",
                (service.episode_id,),  # type: ignore[attr-defined]
            )
        ]
    assert observed, "the fixture message must produce at least one belief"
    assert all(value.endswith("Z") for value in observed)


def test_repeated_refresh_does_not_accumulate_duplicate_evidence_rows(runtime: object) -> None:
    service = _ingest_repeatedly(runtime, "refresh", ("one", "two", "three"))

    assert runtime.store is not None  # type: ignore[attr-defined]
    with runtime.store.connect() as connection:  # type: ignore[attr-defined]
        references = int(
            connection.execute(
                "SELECT COUNT(*) FROM belief_evidence be JOIN beliefs b ON b.id=be.belief_id "
                "WHERE b.episode_id=?",
                (service.episode_id,),  # type: ignore[attr-defined]
            ).fetchone()[0]
        )
        duplicates = int(
            connection.execute(
                "SELECT COUNT(*) FROM (SELECT belief_id,evidence_id,span_json,COUNT(*) AS n "
                "FROM belief_evidence GROUP BY belief_id,evidence_id,span_json HAVING n>1)"
            ).fetchone()[0]
        )
    assert references, "the fixture message must produce at least one evidence reference"
    assert duplicates == 0


# --- Gate: an unencodable argument raised instead of recording a fail-closed decision ---


def test_gate_records_a_decision_for_arguments_canonical_json_cannot_encode(
    tmp_path: Path,
) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite3")
    episode = _episode(store)
    config = packaged_yaml("defaults.yaml")
    gate = ActionGate(store, config, ActionPolicyRegistry(packaged_yaml("action-policies.yaml")))

    decision = gate.evaluate(episode.id, "write_file", {"path": b"\x00binary", "mode": {1, 2}})

    assert decision.outcome in set(GateOutcome)
    recorded = store.events(episode.id)
    assert any(event.kind == "GATE_DECIDED" for event in recorded)


def test_argument_digest_is_stable_and_never_commits_to_a_secret() -> None:
    plain = arguments_digest({"path": "/tmp/x"})
    assert plain == arguments_digest({"path": "/tmp/x"})
    assert len(plain) == 64

    # Values canonical JSON cannot encode still produce a digest rather than an exception.
    assert len(arguments_digest({"blob": b"\x00", "set": {1, 2}})) == 64

    # Secret-like values are removed before hashing, so the digest cannot be used as an
    # oracle to confirm a guessed credential.
    assert arguments_digest({"token": "AKIAIOSFODNN7EXAMPLE"}) == arguments_digest(
        {"token": "AKIAIOSFODNN7DIFFERENT"}
    )
    assert arguments_digest({"token": "AKIAIOSFODNN7EXAMPLE"}) != plain


# --- Enforcement: a vanished co-located projection must fail closed ---


def _binding(episode_id: str, supports: tuple[str, ...] = ()) -> ActionBinding:
    return ActionBinding(
        1,
        episode_id,
        "turn",
        "ns",
        "send",
        "0" * 64,
        "target",
        "policy",
        "rev",
        1,
        "p" * 64,
        "c" * 64,
        "high",
        supports,
        (),
    )


def test_permit_consumption_fails_closed_when_a_colocated_projection_disappears(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = LedgerStore(database)
    episode = _episode(store)
    enforcement = EnforcementStore(database, deterministic_dependencies())
    binding = _binding(episode.id)
    decision = enforcement.issue_action(binding, ttl_seconds=300)

    # The projection was present when the store was opened; losing it afterwards is an
    # integrity failure, not a reason to skip the check.
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE episodes")

    result = enforcement.consume_action(decision.token, binding)
    assert result.consumed is False
    assert result.reason_code == "EPISODE_FINALIZED"


def test_standalone_authorization_store_still_consumes_a_valid_permit(tmp_path: Path) -> None:
    # A store opened on its own database never had the ledger projections, so the callbacks
    # are the contract and binding plus single-use guarantees still apply.
    enforcement = EnforcementStore(tmp_path / "auth.sqlite3", deterministic_dependencies())
    binding = _binding("ep_missing")
    decision = enforcement.issue_action(binding, ttl_seconds=300)

    assert enforcement.consume_action(decision.token, binding).consumed is True
    assert enforcement.consume_action(decision.token, binding).consumed is False


def test_supplied_callbacks_can_still_refuse_a_standalone_permit(tmp_path: Path) -> None:
    enforcement = EnforcementStore(tmp_path / "auth.sqlite3", deterministic_dependencies())
    binding = _binding("ep_missing", supports=("b_one",))
    decision = enforcement.issue_action(binding, ttl_seconds=300)

    result = enforcement.consume_action(
        decision.token, binding, support_is_active=lambda _identifiers: False
    )
    assert result.consumed is False
    assert result.reason_code == "SUPPORT_RETRACTED"


# --- Store: a legacy unscoped idempotency key made replay impossible ---


def test_legacy_unscoped_idempotency_rows_migrate_and_replay_cleanly(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = LedgerStore(database)
    episode = _episode(store)
    store.append_events(
        episode.id,
        [EventDraft("EPISODE_TURN_STARTED", "episode", episode.id, {"current_turn": 2,
                                                                    "updated_at": utc_now()})],
        idempotency_key="turn-2",
    )

    # Reproduce the pre-v7 on-disk shape: the stored key without its episode scope, and the
    # schema stamp rolled back so the migration runs again on reopen.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE idempotency SET idempotency_key=? WHERE idempotency_key=?",
            ("turn-2", f"{episode.id}:turn-2"),
        )
        connection.execute("DELETE FROM schema_migrations WHERE version=?", (LATEST_SCHEMA_VERSION,))

    reopened = LedgerStore(database)
    assert reopened.migration.to_version == LATEST_SCHEMA_VERSION
    with reopened.connect() as connection:
        keys = [str(row[0]) for row in connection.execute("SELECT idempotency_key FROM idempotency")]
    assert keys == [f"{episode.id}:turn-2"]
    assert reopened.replay().deterministic


# --- Contracts: the effective profile must never exceed host capability ---


def test_capability_shortfall_never_reports_an_unavailable_effective_profile() -> None:
    selection = negotiate_profile(
        HostCapabilities(pre_action_gate=True), EnforcementProfile.STRICT
    )
    assert selection.missing
    assert selection.effective is EnforcementProfile.OBSERVE
    assert selection.downgraded is True
    assert "CAPABILITY_SHORTFALL" in selection.reason_codes


def test_tool_invocation_freezes_arguments_for_every_schema_version() -> None:
    context = EpisodeContext.normalize(session_id="s", turn_id="t")
    invocation = ToolInvocation(2, context, "ns", "send", (("payload", {"a": [1]}),))

    payload = invocation.arguments_dict()["payload"]
    with pytest.raises(TypeError):
        payload["a"] = "mutated"  # type: ignore[index]

    with pytest.raises(ValueError, match="single-line string"):
        ToolInvocation(2, context, "ns", "bad\nname", ())


# --- Public API: a blocked output evaluation must not hand back deliverable text ---


def test_blocked_output_evaluation_never_returns_the_candidate_text(tmp_path: Path) -> None:
    from belief_ledger_core import BeliefLedger, OutputCandidate

    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    config = packaged_yaml("defaults.yaml")
    config["lint"]["med"] = "rewrite_once"
    ledger = BeliefLedger.open(state_root=state_root, config=config)
    context = EpisodeContext.normalize(session_id="s", turn_id="t")
    handle = ledger.start_episode(context)

    secret_claim = "The production database was migrated at midnight."
    evaluation = ledger.evaluate_output(
        handle.id, OutputCandidate(1, context, secret_claim, "med", True)
    )

    assert evaluation.accepted is False
    payload = evaluation.payload.decode("utf-8")
    assert secret_claim not in payload or payload.startswith("Response blocked")


# --- Manifest: v1-only keys must not be silently ignored in a v2 rule ---


def test_v2_policy_rule_rejects_v1_only_spellings() -> None:
    from belief_ledger_core.manifest import ManifestError, ToolPolicyManifest

    rule = {
        "id": "send",
        "revision": "1",
        "effectful": True,
        "base_stakes": "high",
        "exact": ["send"],
        "allow_human_approval": True,
    }
    with pytest.raises(ManifestError, match="unknown policy fields"):
        ToolPolicyManifest.load({"schema_version": 2, "rules": [rule]})

    # The v1 registry still accepts them and translates them into the v2 spelling.
    v1 = json.loads(json.dumps({"schema_version": 1, "rules": [rule]}))
    manifest = ToolPolicyManifest.load(v1)
    matched = manifest.match("send")
    assert matched is not None and matched.approval_policy == "allowed"
