"""Versioned configuration loading, merging, validation, and state paths."""

from __future__ import annotations

import copy
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from . import data as data_package
from .events import canonical_json, content_hash
from .models import Stakes

CONFIG_ENV = "BELIEF_LEDGER_PRAMANA_CONFIG"
PLUGIN_STATE_DIR = "belief-ledger-pramana"


class ConfigError(ValueError):
    """Raised when configuration cannot be safely accepted."""


@dataclass(frozen=True, slots=True)
class StatePaths:
    hermes_home: Path
    root: Path
    config: Path
    database: Path
    evidence: Path
    exports: Path
    locks: Path


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    data: dict[str, Any]
    source: Path | None
    warnings: tuple[str, ...]
    digest: str
    mtime_ns: int | None

    @property
    def mode(self) -> str:
        return str(self.data["mode"])

    @property
    def default_stakes(self) -> Stakes:
        return Stakes(str(self.data["default_stakes"]))

    def section(self, name: str) -> dict[str, Any]:
        section = self.data.get(name)
        if not isinstance(section, dict):
            raise ConfigError(f"configuration section {name!r} is not a mapping")
        return copy.deepcopy(section)


def get_hermes_home() -> Path:
    """Resolve the profile-local Hermes home without appending a profile name."""

    try:
        from hermes_constants import (  # type: ignore[import-untyped]
            get_hermes_home as host_get_hermes_home,
        )

        return Path(host_get_hermes_home()).expanduser().resolve()
    except (ImportError, AttributeError, OSError):
        configured = os.environ.get("HERMES_HOME")
        return Path(configured or "~/.hermes").expanduser().resolve()


def state_paths(hermes_home: Path | None = None, database: str | None = None) -> StatePaths:
    home = (hermes_home or get_hermes_home()).expanduser().resolve()
    root = home / PLUGIN_STATE_DIR
    database_path = Path(database).expanduser().resolve() if database else root / "ledger.sqlite3"
    return StatePaths(
        hermes_home=home,
        root=root,
        config=root / "config.yaml",
        database=database_path,
        evidence=root / "evidence",
        exports=root / "exports",
        locks=root / "locks",
    )


def ensure_state_directories(paths: StatePaths) -> None:
    """Create private mutable-state directories."""

    for path in (paths.root, paths.evidence, paths.exports, paths.locks, paths.database.parent):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        _chmod_if_posix(path, 0o700)


def packaged_yaml(name: str) -> dict[str, Any]:
    resource = resources.files(data_package).joinpath(name)
    parsed = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ConfigError(f"packaged {name} is not a mapping")
    return parsed


def load_config(
    *,
    hermes_home: Path | None = None,
    explicit_path: Path | None = None,
    initialize: bool = True,
) -> tuple[ConfigSnapshot, StatePaths]:
    """Load a single immutable turn snapshot using the required precedence."""

    defaults = packaged_yaml("defaults.yaml")
    initial_paths = state_paths(hermes_home)
    requested = explicit_path
    if requested is None and os.environ.get(CONFIG_ENV):
        requested = Path(os.environ[CONFIG_ENV]).expanduser()

    if initialize:
        ensure_state_directories(initial_paths)
        if requested is None and not initial_paths.config.exists():
            _atomic_write(initial_paths.config, yaml.safe_dump(defaults, sort_keys=False), 0o600)

    source = requested or (initial_paths.config if initial_paths.config.exists() else None)
    override: dict[str, Any] = {}
    if source is not None:
        source = source.resolve()
        if not source.is_file():
            raise ConfigError(f"configuration file does not exist: {source}")
        parsed = yaml.safe_load(source.read_text(encoding="utf-8"))
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ConfigError("configuration root must be a mapping")
        override = parsed

    mode_hint = str(override.get("mode", defaults.get("mode", "enforce"))).lower()
    warnings: list[str] = []
    unknown = _unknown_paths(override, defaults)
    if unknown:
        messages = [f"unknown configuration key: {item}" for item in unknown]
        if mode_hint == "observe":
            warnings.extend(messages)
        else:
            raise ConfigError("; ".join(messages))

    merged = _deep_merge(defaults, override)
    warnings.extend(validate_config(merged))
    database_value = merged["storage"].get("database")
    paths = state_paths(hermes_home, str(database_value) if database_value else None)
    if initialize:
        ensure_state_directories(paths)
    mtime_ns = source.stat().st_mtime_ns if source and source.exists() else None
    snapshot = ConfigSnapshot(
        data=merged,
        source=source,
        warnings=tuple(warnings),
        digest=content_hash(canonical_json(merged)),
        mtime_ns=mtime_ns,
    )
    return snapshot, paths


def validate_config(config: dict[str, Any]) -> list[str]:
    """Validate all safety-sensitive values and return non-fatal warnings."""

    if config.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    if config.get("mode") not in {"observe", "warn", "enforce"}:
        raise ConfigError("mode must be observe, warn, or enforce")
    try:
        Stakes(str(config.get("default_stakes")))
    except ValueError as exc:
        raise ConfigError("default_stakes is invalid") from exc

    storage = _mapping(config, "storage")
    if storage.get("evidence_mode") not in {"hash_only", "excerpt", "full"}:
        raise ConfigError("storage.evidence_mode must be hash_only, excerpt, or full")
    _bounded_int(storage, "max_excerpt_chars", 0, 1_000_000)
    _bounded_int(storage, "busy_timeout_ms", 1, 120_000)

    context = _mapping(config, "context")
    _bounded_int(context, "max_chars", 512, 8_000)
    _bounded_int(context, "max_beliefs", 1, 1_000)
    _bounded_int(context, "max_graph_depth", 0, 32)

    ingestion = _mapping(config, "ingestion")
    _bounded_int(ingestion, "max_claims_per_evidence", 0, 200)
    _bounded_int(ingestion, "max_unpromoted_per_request", 0, 50)
    _bounded_int(ingestion, "max_atomic_claim_words", 5, 100)
    threshold = ingestion.get("near_duplicate_threshold")
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not 0.0 <= float(threshold) <= 1.0
    ):
        raise ConfigError("ingestion.near_duplicate_threshold must be in [0,1]")

    verification = _mapping(config, "verification")
    for key in (
        "max_llm_calls_per_turn",
        "max_llm_calls_per_episode",
        "max_input_tokens_per_episode",
        "max_output_tokens_per_episode",
        "structured_timeout_seconds",
    ):
        _bounded_int(verification, key, 0, 10_000_000)

    lint = _mapping(config, "lint")
    allowed_lint = {"annotate", "rewrite_once", "block", "allow"}
    for stake in ("low", "med", "high", "critical"):
        if lint.get(stake) not in allowed_lint:
            raise ConfigError(f"lint.{stake} is invalid")
    if int(lint.get("max_rewrite_attempts", -1)) > 1:
        raise ConfigError("lint.max_rewrite_attempts may not exceed 1")

    gating = _mapping(config, "gating")
    if gating.get("unknown_tool_policy") not in {"conservative", "allow_read_only"}:
        raise ConfigError("gating.unknown_tool_policy is invalid")
    if gating.get("fail_closed_at") not in {"high", "critical"}:
        raise ConfigError("gating.fail_closed_at must be high or critical")
    _string_paths(gating, "policy_files")

    priority = _mapping(config, "priority")
    integrity = _mapping(priority, "integrity_rank")
    if not all(isinstance(integrity.get(key), int) for key in ("trusted", "semi", "untrusted")):
        raise ConfigError("priority.integrity_rank must define integer ranks")

    trust = _mapping(config, "trust")
    _string_paths(trust, "source_profile_files")
    matrix = _mapping(trust, "matrix")
    required_profiles = {
        "pratyaksha_tool",
        "shabda_internal_trusted",
        "shabda_web_semi",
        "shabda_web_untrusted",
        "user_self",
        "user_world",
        "anumana_registered",
        "anupalabdhi",
    }
    missing_profiles = sorted(required_profiles - set(matrix))
    if missing_profiles:
        raise ConfigError(f"trust.matrix missing profiles: {', '.join(missing_profiles)}")
    for profile in required_profiles:
        row = _mapping(matrix, profile)
        for stake in ("low", "med", "high", "critical"):
            cell = _mapping(row, stake)
            if cell.get("mode") not in {"svatah", "paratah", "quarantine", "yogyata", "reject"}:
                raise ConfigError(f"trust.matrix.{profile}.{stake}.mode is invalid")
            _bounded_int(cell, "k", 0, 20)
    yogyata = _mapping(trust, "yogyata")
    for key in ("min_coverage", "min_recall"):
        value = yogyata.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= float(value) <= 1.0
        ):
            raise ConfigError(f"trust.yogyata.{key} must be in [0,1]")
    return []


def config_needs_reload(snapshot: ConfigSnapshot) -> bool:
    if snapshot.source is None or snapshot.mtime_ns is None:
        return False
    try:
        return snapshot.source.stat().st_mtime_ns != snapshot.mtime_ns
    except OSError:
        return True


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _unknown_paths(
    candidate: dict[str, Any], template: dict[str, Any], prefix: str = ""
) -> list[str]:
    unknown: list[str] = []
    for key, value in candidate.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in template:
            unknown.append(path)
        elif isinstance(value, dict) and isinstance(template[key], dict) and template[key]:
            # Empty maps are declared operator extension points.
            unknown.extend(_unknown_paths(value, template[key], path))
    return unknown


def _mapping(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _bounded_int(container: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be an integer in [{minimum},{maximum}]")
    return value


def _string_paths(container: dict[str, Any], key: str) -> tuple[str, ...]:
    value = container.get(key, [])
    if (
        not isinstance(value, list)
        or len(value) > 20
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ConfigError(f"{key} must be a list of at most 20 non-empty paths")
    return tuple(value)


def _atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_if_posix(tmp_path, mode)
        os.replace(tmp_path, path)
        _chmod_if_posix(path, mode)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def _chmod_if_posix(path: Path, mode: int) -> None:
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        if current != mode:
            path.chmod(mode)
    except OSError:
        return
