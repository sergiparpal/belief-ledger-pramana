from __future__ import annotations

import copy
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from belief_ledger_pramana.config import (
    ConfigError,
    config_needs_reload,
    load_config,
    packaged_yaml,
    validate_config,
)


def test_config_is_initialized_privately(tmp_path: Path) -> None:
    snapshot, paths = load_config(hermes_home=tmp_path)
    assert snapshot.data["mode"] == "enforce"
    assert paths.config.exists()
    if os.name != "nt":
        assert stat.S_IMODE(paths.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(paths.config.stat().st_mode) == 0o600
    assert paths.database.parent == paths.root


def test_unknown_key_rejected_in_enforce(tmp_path: Path) -> None:
    config = tmp_path / "custom.yaml"
    config.write_text("schema_version: 1\nmode: enforce\nunknown: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(hermes_home=tmp_path / "home", explicit_path=config)


def test_unknown_key_warns_in_observe(tmp_path: Path) -> None:
    config = tmp_path / "custom.yaml"
    config.write_text("schema_version: 1\nmode: observe\nunknown: true\n", encoding="utf-8")
    snapshot, _ = load_config(hermes_home=tmp_path / "home", explicit_path=config)
    assert snapshot.mode == "observe"
    assert snapshot.warnings == ("unknown configuration key: unknown",)


def test_unsafe_context_budget_rejected(tmp_path: Path) -> None:
    config = tmp_path / "custom.yaml"
    config.write_text("context:\n  max_chars: 9000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_chars"):
        load_config(hermes_home=tmp_path / "home", explicit_path=config)


def test_environment_override_has_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "external.yaml"
    explicit.write_text("mode: warn\n", encoding="utf-8")
    monkeypatch.setenv("BELIEF_LEDGER_PRAMANA_CONFIG", str(explicit))
    snapshot, _ = load_config(hermes_home=tmp_path / "home")
    assert snapshot.mode == "warn"
    assert snapshot.source == explicit.resolve()


def test_relative_external_database_resolves_from_config_without_chmodding_parent(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "external"
    config_dir.mkdir(mode=0o755)
    config = config_dir / "config.yaml"
    config.write_text("storage:\n  database: state/ledger.sqlite3\n", encoding="utf-8")
    _, paths = load_config(hermes_home=tmp_path / "home", explicit_path=config)
    assert paths.database == (config_dir / "state" / "ledger.sqlite3").resolve()
    if os.name != "nt":
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o755


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("schema_version",), 2, "schema_version"),
        (("mode",), "permissive", "mode"),
        (("default_stakes",), "extreme", "default_stakes"),
        (("storage",), [], "storage"),
        (("storage", "evidence_mode"), "raw", "evidence_mode"),
        (("storage", "max_excerpt_chars"), True, "max_excerpt_chars"),
        (("context", "max_beliefs"), 0, "max_beliefs"),
        (("context", "max_graph_depth"), 100, "max_graph_depth"),
        (("ingestion", "max_atomic_claim_words"), 2, "max_atomic_claim_words"),
        (("ingestion", "near_duplicate_threshold"), True, "near_duplicate_threshold"),
        (("verification", "max_llm_calls_per_turn"), -1, "max_llm_calls_per_turn"),
        (("lint", "med"), "loop", "lint.med"),
        (("lint", "max_rewrite_attempts"), 2, "max_rewrite_attempts"),
        (("gating", "unknown_tool_policy"), "guess", "unknown_tool_policy"),
        (("gating", "fail_closed_at"), "med", "fail_closed_at"),
        (("gating", "policy_files"), [""], "policy_files"),
        (("priority", "integrity_rank", "trusted"), "high", "integrity_rank"),
        (("trust", "source_profile_files"), "x", "source_profile_files"),
        (("trust", "matrix", "pratyaksha_tool", "med", "mode"), "maybe", "mode"),
        (("trust", "matrix", "pratyaksha_tool", "med", "k"), 99, "^k"),
        (("trust", "yogyata", "min_coverage"), True, "min_coverage"),
    ],
)
def test_every_safety_sensitive_config_family_rejects_invalid_values(
    path: tuple[str, ...], value: object, message: str
) -> None:
    config = copy.deepcopy(packaged_yaml("defaults.yaml"))
    target = config
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ConfigError, match=message):
        validate_config(config)


def test_config_reload_detection_handles_changes_and_disappearance(tmp_path: Path) -> None:
    snapshot, _ = load_config(hermes_home=tmp_path)
    assert not config_needs_reload(snapshot)
    assert snapshot.source is not None
    assert snapshot.mtime_ns is not None
    updated_mtime_ns = snapshot.mtime_ns + 1_000_000_000
    os.utime(snapshot.source, ns=(updated_mtime_ns, updated_mtime_ns))
    assert config_needs_reload(snapshot)
    snapshot.source.unlink()
    assert config_needs_reload(snapshot)

    packaged = replace(snapshot, source=None, mtime_ns=None)
    assert not config_needs_reload(packaged)
