"""Versioned configuration loading, merging, validation, and state paths."""

from __future__ import annotations

import copy
import os
import stat
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from . import data as data_package
from .atomic import write_private_text_atomically
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
    integrity_key: Path


@dataclass(frozen=True, slots=True)
class StorageSettings:
    evidence_mode: str
    max_excerpt_chars: int
    redact_secrets: bool
    busy_timeout_ms: int


@dataclass(frozen=True, slots=True)
class ContextSettings:
    max_chars: int
    max_beliefs: int
    max_graph_depth: int
    relevance: str


@dataclass(frozen=True, slots=True)
class IngestionSettings:
    lazy_claim_extraction: bool
    max_claims_per_evidence: int
    max_unpromoted_per_request: int
    near_duplicate_threshold: float


@dataclass(frozen=True, slots=True)
class VerificationSettings:
    max_llm_calls_per_turn: int
    max_llm_calls_per_episode: int
    max_input_tokens_per_episode: int
    max_output_tokens_per_episode: int
    structured_timeout_seconds: int
    critical_human_confirmation: bool


@dataclass(frozen=True, slots=True)
class LintSettings:
    low: str
    med: str
    high: str
    critical: str
    max_rewrite_attempts: int
    pending_marker: str


@dataclass(frozen=True, slots=True)
class GatingSettings:
    enabled: bool
    unknown_tool_policy: str
    fail_closed_at: Stakes
    allow_human_approval: bool
    confirmation_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Typed, immutable configuration consumed at application boundaries.

    The raw snapshot remains available for pure domain policy functions during
    the migration, but adapters and use cases should consume these sections.
    """

    mode: str
    default_stakes: Stakes
    storage: StorageSettings
    context: ContextSettings
    ingestion: IngestionSettings
    verification: VerificationSettings
    lint: LintSettings
    gating: GatingSettings


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

    @property
    def settings(self) -> RuntimeSettings:
        """Return the typed application view of this validated snapshot."""

        storage = self.data["storage"]
        context = self.data["context"]
        ingestion = self.data["ingestion"]
        verification = self.data["verification"]
        lint = self.data["lint"]
        gating = self.data["gating"]
        return RuntimeSettings(
            mode=self.mode,
            default_stakes=self.default_stakes,
            storage=StorageSettings(
                evidence_mode=str(storage["evidence_mode"]),
                max_excerpt_chars=int(storage["max_excerpt_chars"]),
                redact_secrets=bool(storage["redact_secrets"]),
                busy_timeout_ms=int(storage["busy_timeout_ms"]),
            ),
            context=ContextSettings(
                max_chars=int(context["max_chars"]),
                max_beliefs=int(context["max_beliefs"]),
                max_graph_depth=int(context["max_graph_depth"]),
                relevance=str(context["relevance"]),
            ),
            ingestion=IngestionSettings(
                lazy_claim_extraction=bool(ingestion["lazy_claim_extraction"]),
                max_claims_per_evidence=int(ingestion["max_claims_per_evidence"]),
                max_unpromoted_per_request=int(ingestion["max_unpromoted_per_request"]),
                near_duplicate_threshold=float(ingestion["near_duplicate_threshold"]),
            ),
            verification=VerificationSettings(
                max_llm_calls_per_turn=int(verification["max_llm_calls_per_turn"]),
                max_llm_calls_per_episode=int(verification["max_llm_calls_per_episode"]),
                max_input_tokens_per_episode=int(verification["max_input_tokens_per_episode"]),
                max_output_tokens_per_episode=int(verification["max_output_tokens_per_episode"]),
                structured_timeout_seconds=int(verification["structured_timeout_seconds"]),
                critical_human_confirmation=bool(verification["critical_human_confirmation"]),
            ),
            lint=LintSettings(
                low=str(lint["low"]),
                med=str(lint["med"]),
                high=str(lint["high"]),
                critical=str(lint["critical"]),
                max_rewrite_attempts=int(lint["max_rewrite_attempts"]),
                pending_marker=str(lint["pending_marker"]),
            ),
            gating=GatingSettings(
                enabled=bool(gating["enabled"]),
                unknown_tool_policy=str(gating["unknown_tool_policy"]),
                fail_closed_at=Stakes(str(gating["fail_closed_at"])),
                allow_human_approval=bool(gating["allow_human_approval"]),
                confirmation_ttl_seconds=int(gating["confirmation_ttl_seconds"]),
            ),
        )


def settings_from_data(data: dict[str, Any]) -> RuntimeSettings:
    """Build typed settings for a validated legacy configuration mapping.

    This compatibility bridge lets callers migrate independently while new
    composition roots pass :class:`ConfigSnapshot` directly.
    """

    return ConfigSnapshot(data, None, (), "", None).settings


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


def state_paths(
    hermes_home: Path | None = None,
    database: str | None = None,
    *,
    database_base: Path | None = None,
) -> StatePaths:
    home = (hermes_home or get_hermes_home()).expanduser().resolve()
    root = home / PLUGIN_STATE_DIR
    if database:
        configured = Path(database).expanduser()
        database_path = (
            configured
            if configured.is_absolute()
            else (database_base or root).expanduser() / configured
        ).resolve()
    else:
        database_path = root / "ledger.sqlite3"
    if not _is_within(database_path, root):
        raise ConfigError("storage.database must resolve inside the plugin state directory")
    return StatePaths(
        hermes_home=home,
        root=root,
        config=root / "config.yaml",
        database=database_path,
        evidence=root / "evidence",
        exports=root / "exports",
        locks=root / "locks",
        integrity_key=root / "locks" / "ledger.integrity.key",
    )


def configured_config_path(
    hermes_home: Path | None = None, *, explicit_path: Path | None = None
) -> Path:
    """Return the configured path even when its YAML is currently invalid.

    The runtime uses this for a degraded fallback snapshot so a repaired file
    can be noticed and loaded at the next turn boundary.
    """

    paths = state_paths(hermes_home)
    requested = explicit_path
    if requested is None and os.environ.get(CONFIG_ENV):
        requested = Path(os.environ[CONFIG_ENV]).expanduser()
    source = (requested or paths.config).expanduser().resolve()
    if not _is_within(source, paths.root):
        raise ConfigError("configuration file must be inside the plugin state directory")
    return source


def ensure_state_directories(paths: StatePaths) -> None:
    """Create private mutable-state directories."""

    managed = [paths.root, paths.evidence, paths.exports, paths.locks]
    paths.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    managed.extend(_directories_within(paths.root, paths.database.parent))
    for path in dict.fromkeys(managed):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        _chmod_if_posix(path, 0o700)
        require_private_path(path, "state directory", directory=True)
    if paths.database.is_symlink():
        require_private_path(paths.database, "ledger database")
    elif paths.database.exists():
        _chmod_if_posix(paths.database, 0o600)
        require_private_path(paths.database, "ledger database")


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
        if not _is_within(source, initial_paths.root):
            raise ConfigError("configuration file must be inside the plugin state directory")
        if not source.is_file():
            raise ConfigError(f"configuration file does not exist: {source}")
        require_private_path(source, "configuration file")
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
    paths = state_paths(
        hermes_home,
        str(database_value) if database_value else None,
        database_base=source.parent if source is not None else initial_paths.config.parent,
    )
    if initialize:
        ensure_state_directories(paths)
    _resolve_private_extension_paths(merged, paths.root)
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

    enforcement = _mapping(config, "enforcement")
    if enforcement.get("requested_profile") not in {
        "observe",
        "action_enforce",
        "accepted_final",
        "strict",
    }:
        raise ConfigError("enforcement.requested_profile is invalid")
    if not isinstance(enforcement.get("allow_diagnostic_downgrade"), bool):
        raise ConfigError("enforcement.allow_diagnostic_downgrade must be a boolean")

    storage = _mapping(config, "storage")
    if storage.get("evidence_mode") not in {"hash_only", "excerpt", "full"}:
        raise ConfigError("storage.evidence_mode must be hash_only, excerpt, or full")
    if not isinstance(storage.get("redact_secrets"), bool):
        raise ConfigError("storage.redact_secrets must be a boolean")
    _bounded_int(storage, "max_excerpt_chars", 0, 1_000_000)
    _bounded_int(storage, "busy_timeout_ms", 1, 120_000)

    context = _mapping(config, "context")
    _bounded_int(context, "max_chars", 512, 8_000)
    _bounded_int(context, "max_beliefs", 1, 1_000)
    _bounded_int(context, "max_graph_depth", 0, 32)
    if context.get("relevance") not in {"fts5", "none"}:
        raise ConfigError("context.relevance must be fts5 or none")

    ingestion = _mapping(config, "ingestion")
    _bounded_int(ingestion, "max_claims_per_evidence", 0, 200)
    _bounded_int(ingestion, "max_unpromoted_per_request", 0, 50)
    _bounded_int(ingestion, "max_atomic_claim_words", 5, 100)
    _bounded_int(ingestion, "max_atomic_claim_chars", 80, 20_000)
    if not isinstance(ingestion.get("lazy_claim_extraction"), bool):
        raise ConfigError("ingestion.lazy_claim_extraction must be a boolean")
    if not isinstance(ingestion.get("trusted_workspace_files"), bool):
        raise ConfigError("ingestion.trusted_workspace_files must be a boolean")
    threshold = ingestion.get("near_duplicate_threshold")
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not 0.0 <= float(threshold) <= 1.0
    ):
        raise ConfigError("ingestion.near_duplicate_threshold must be in [0,1]")

    ttl = _mapping(config, "perishability_ttl")
    for name in ("stable", "slow", "fast", "live"):
        value = ttl.get(f"{name}_seconds")
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 31_536_000
        ):
            raise ConfigError(
                f"perishability_ttl.{name}_seconds must be null or an integer in [0,31536000]"
            )

    verification = _mapping(config, "verification")
    for key in (
        "max_llm_calls_per_turn",
        "max_llm_calls_per_episode",
        "max_input_tokens_per_episode",
        "max_output_tokens_per_episode",
        "structured_timeout_seconds",
    ):
        _bounded_int(verification, key, 0, 10_000_000)
    if not isinstance(verification.get("critical_human_confirmation"), bool):
        raise ConfigError("verification.critical_human_confirmation must be a boolean")

    lint = _mapping(config, "lint")
    allowed_lint = {"annotate", "rewrite_once", "block", "allow"}
    for stake in ("low", "med", "high", "critical"):
        if lint.get(stake) not in allowed_lint:
            raise ConfigError(f"lint.{stake} is invalid")
    _bounded_int(lint, "max_rewrite_attempts", 0, 1)
    marker = lint.get("pending_marker")
    if (
        not isinstance(marker, str)
        or not marker.strip()
        or len(marker) > 128
        or "\n" in marker
        or "\r" in marker
    ):
        raise ConfigError(
            "lint.pending_marker must be a non-empty single-line string of at most 128 characters"
        )

    gating = _mapping(config, "gating")
    if gating.get("unknown_tool_policy") not in {"conservative", "allow_read_only"}:
        raise ConfigError("gating.unknown_tool_policy is invalid")
    if gating.get("fail_closed_at") not in {"high", "critical"}:
        raise ConfigError("gating.fail_closed_at must be high or critical")
    _bounded_int(gating, "confirmation_ttl_seconds", 1, 86_400)
    if not isinstance(gating.get("enabled"), bool) or not isinstance(
        gating.get("allow_human_approval"), bool
    ):
        raise ConfigError("gating enabled and allow_human_approval must be booleans")
    _string_paths(gating, "policy_files")

    priority = _mapping(config, "priority")
    integrity = _mapping(priority, "integrity_rank")
    if not all(isinstance(integrity.get(key), int) for key in ("trusted", "semi", "untrusted")):
        raise ConfigError("priority.integrity_rank must define integer ranks")
    bands = _mapping(priority, "reliability_bands")
    high = bands.get("high")
    medium = bands.get("medium")
    if (
        isinstance(high, bool)
        or isinstance(medium, bool)
        or not isinstance(high, (int, float))
        or not isinstance(medium, (int, float))
        or not 0 <= float(medium) <= float(high) <= 1
    ):
        raise ConfigError("priority.reliability_bands must satisfy 0 <= medium <= high <= 1")

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
    if snapshot.source is None:
        return False
    try:
        current_mtime = snapshot.source.stat().st_mtime_ns
    except OSError:
        return snapshot.mtime_ns is not None
    return snapshot.mtime_ns is None or current_mtime != snapshot.mtime_ns


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
    write_private_text_atomically(path, text, mode=mode)


def _chmod_if_posix(path: Path, mode: int) -> None:
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        if current != mode:
            path.chmod(mode)
    except OSError:
        return


def require_private_path(path: Path, label: str, *, directory: bool = False) -> None:
    """Reject links and state paths readable by non-owner principals."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConfigError(f"unable to inspect {label}: {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ConfigError(f"{label} must not be a symbolic link: {path}")
    if directory and not stat.S_ISDIR(metadata.st_mode):
        raise ConfigError(f"{label} must be a directory: {path}")
    if not directory and not stat.S_ISREG(metadata.st_mode):
        raise ConfigError(f"{label} must be a regular file: {path}")
    if os.name == "nt":
        _require_private_windows_acl(path, label)
        return
    getuid = getattr(os, "getuid", None)
    if not callable(getuid):
        raise ConfigError("unable to verify current-user ownership on this platform")
    if metadata.st_uid != getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ConfigError(
            f"{label} must be owned by the current user and inaccessible to group/other"
        )


def _resolve_private_extension_paths(config: dict[str, Any], root: Path) -> None:
    for section, key, label in (
        ("gating", "policy_files", "action policy extension"),
        ("trust", "source_profile_files", "source profile extension"),
    ):
        values = _mapping(config, section).get(key, [])
        resolved: list[str] = []
        for raw_path in values:
            candidate = Path(str(raw_path)).expanduser()
            path = (candidate if candidate.is_absolute() else root / candidate).resolve()
            if not _is_within(path, root):
                raise ConfigError(f"{label} must be inside the plugin state directory: {path}")
            if not path.is_file() or path.stat().st_size > 1_000_000:
                raise ConfigError(f"{label} is unavailable or too large: {path}")
            require_private_path(path, label)
            resolved.append(str(path))
        _mapping(config, section)[key] = resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _directories_within(root: Path, target: Path) -> tuple[Path, ...]:
    directories: list[Path] = []
    current = target
    while True:
        directories.append(current)
        if current == root:
            return tuple(reversed(directories))
        current = current.parent


def _require_private_windows_acl(path: Path, label: str) -> None:
    """Reject ACLs granting broad Windows principals access to sensitive state."""

    try:
        result = subprocess.run(
            ["icacls", str(path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise ConfigError(f"unable to inspect ACL for {label}: {path}: {exc}") from exc
    if result.returncode != 0:
        raise ConfigError(f"unable to inspect ACL for {label}: {path}: {result.stderr.strip()}")
    broad_principals = {
        "everyone",
        "nt authority\\authenticated users",
        "builtin\\users",
        "builtin\\guests",
        "users",
        "guests",
    }
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        principal = line.split(":", 1)[0].strip().casefold()
        if principal in broad_principals:
            raise ConfigError(f"{label} ACL grants a broad principal access: {path}")
