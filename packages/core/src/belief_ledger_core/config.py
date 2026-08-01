"""Host-neutral configuration loader with an explicit state root."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .events import canonical_json, content_hash
from .immutable import FrozenDict, FrozenList, freeze

__all__ = [
    "CoreConfig",
    "CoreConfigSnapshot",
    "FrozenDict",
    "FrozenList",
    "freeze_config",
    "load_core_config",
    "validate_core_config",
]


@dataclass(frozen=True, slots=True)
class CoreConfig:
    """Public host-neutral configuration input.

    ``values`` is merged over packaged defaults. Hosts resolve their own configuration paths and
    pass the resulting values here; core never probes host-specific locations.
    """

    schema_version: int = 1
    values: dict[str, Any] | None = None
    explicit_path: Path | None = None

    def __post_init__(self) -> None:
        if self.values is not None:
            object.__setattr__(self, "values", freeze(self.values))


def freeze_config(value: Any) -> Any:
    return freeze(value)


@dataclass(frozen=True, slots=True)
class CoreConfigSnapshot:
    schema_version: int
    state_root: Path
    source: Path | None
    data: dict[str, Any]
    digest: str

    def __post_init__(self) -> None:
        frozen = freeze_config(self.data)
        object.__setattr__(self, "data", frozen)
        object.__setattr__(self, "digest", content_hash(canonical_json(frozen)))


def load_core_config(
    state_root: Path,
    *,
    defaults: dict[str, Any],
    explicit_path: Path | None = None,
) -> CoreConfigSnapshot:
    """Load defaults plus an optional adapter-resolved file; never inspect host state."""

    root = state_root.expanduser().resolve()
    source = explicit_path.expanduser().resolve() if explicit_path is not None else None
    override: dict[str, Any] = {}
    if source is not None:
        parsed = yaml.safe_load(source.read_text(encoding="utf-8"))
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError("core configuration root must be a mapping")
        override = parsed
    data = _merge(defaults, override)
    validate_core_config(data, defaults=defaults)
    return CoreConfigSnapshot(1, root, source, data, content_hash(canonical_json(data)))


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: _mutable_copy(value) for key, value in base.items()}
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = _mutable_copy(value)
    return result


def validate_core_config(data: dict[str, Any], *, defaults: dict[str, Any]) -> None:
    """Validate public-core configuration before any files or policy state are opened."""

    unknown = _unknown_paths(data, defaults)
    if unknown:
        raise ValueError(f"unknown configuration key: {unknown[0]}")
    # The loader is also a public merge utility. Custom caller schemas still
    # receive unknown-key validation; the packaged schema receives the full
    # set of safety checks below.
    if "schema_version" not in defaults:
        return
    if data.get("schema_version") != 1 or type(data.get("schema_version")) is not int:
        raise ValueError("schema_version must be 1")
    if data.get("mode") not in {"observe", "warn", "enforce"}:
        raise ValueError("mode must be observe, warn, or enforce")
    if data.get("default_stakes") not in {"low", "med", "high", "critical"}:
        raise ValueError("default_stakes is invalid")
    _boolean(data, "enabled")

    enforcement = _mapping(data, "enforcement")
    if enforcement.get("requested_profile") not in {
        "observe",
        "action_enforce",
        "accepted_final",
        "strict",
    }:
        raise ValueError("enforcement.requested_profile is invalid")
    _boolean(enforcement, "allow_diagnostic_downgrade")

    storage = _mapping(data, "storage")
    database = storage.get("database")
    if database is not None and (
        not isinstance(database, str) or not database.strip() or "\x00" in database
    ):
        raise ValueError("storage.database must be null or a non-empty path")
    if storage.get("evidence_mode") not in {"hash_only", "excerpt", "full"}:
        raise ValueError("storage.evidence_mode is invalid")
    _boolean(storage, "redact_secrets")
    _bounded_int(storage, "max_excerpt_chars", 0, 1_000_000)
    _bounded_int(storage, "busy_timeout_ms", 1, 120_000)

    context = _mapping(data, "context")
    _bounded_int(context, "max_chars", 512, 1_000_000)
    _bounded_int(context, "max_beliefs", 1, 20_000)
    _bounded_int(context, "max_graph_depth", 0, 32)
    _bounded_int(context, "retraction_ttl_turns", 1, 10_000)
    _boolean(context, "pending_only_when_relevant")
    if context.get("relevance") not in {"fts5", "none"}:
        raise ValueError("context.relevance is invalid")

    ingestion = _mapping(data, "ingestion")
    for key, minimum, maximum in (
        ("max_claims_per_evidence", 0, 200),
        ("max_unpromoted_per_request", 0, 50),
        ("max_atomic_claim_words", 5, 100),
        ("max_atomic_claim_chars", 80, 20_000),
    ):
        _bounded_int(ingestion, key, minimum, maximum)
    _boolean(ingestion, "lazy_claim_extraction")
    _boolean(ingestion, "trusted_workspace_files")
    _bounded_number(ingestion, "near_duplicate_threshold", 0.0, 1.0)

    verification = _mapping(data, "verification")
    for key in (
        "max_llm_calls_per_turn",
        "max_llm_calls_per_episode",
        "max_input_tokens_per_episode",
        "max_output_tokens_per_episode",
    ):
        _bounded_int(verification, key, 0, 10_000_000)
    _bounded_int(verification, "structured_timeout_seconds", 1, 10_000_000)
    _boolean(verification, "critical_human_confirmation")

    lint = _mapping(data, "lint")
    for stake in ("low", "med", "high", "critical"):
        if lint.get(stake) not in {"allow", "annotate", "rewrite_once", "block"}:
            raise ValueError(f"lint.{stake} is invalid")
    _bounded_int(lint, "max_rewrite_attempts", 0, 1)
    marker = lint.get("pending_marker")
    if not isinstance(marker, str) or not marker.strip() or len(marker) > 128 or "\n" in marker:
        raise ValueError("lint.pending_marker is invalid")

    gating = _mapping(data, "gating")
    _boolean(gating, "enabled")
    _boolean(gating, "allow_human_approval")
    if gating.get("unknown_tool_policy") not in {"conservative", "allow_read_only"}:
        raise ValueError("gating.unknown_tool_policy is invalid")
    if gating.get("fail_closed_at") not in {"high", "critical"}:
        raise ValueError("gating.fail_closed_at is invalid")
    _bounded_int(gating, "confirmation_ttl_seconds", 1, 86_400)
    _string_list(gating, "policy_files")

    priority = _mapping(data, "priority")
    integrity_rank = _rank_mapping(_mapping(priority, "integrity_rank"), "priority.integrity_rank")
    if not (
        integrity_rank.get("trusted", -1)
        > integrity_rank.get("semi", -1)
        > integrity_rank.get("untrusted", -1)
    ):
        raise ValueError("priority.integrity_rank must satisfy trusted > semi > untrusted")
    type_rank = _mapping(priority, "type_rank")
    if not type_rank:
        raise ValueError("priority.type_rank must not be empty")
    for profile, ranks in type_rank.items():
        _rank_mapping(ranks, f"priority.type_rank.{profile}")
    domain_profiles = _mapping(priority, "domain_profiles")
    for profile, ranks in domain_profiles.items():
        _rank_mapping(ranks, f"priority.domain_profiles.{profile}")
    reliability = _mapping(priority, "reliability_bands")
    medium = _number(reliability, "medium")
    high = _number(reliability, "high")
    if not 0 <= medium <= high <= 1:
        raise ValueError("priority.reliability_bands must satisfy 0 <= medium <= high <= 1")
    specificity = priority.get("specificity_keys")
    if (
        not isinstance(specificity, list)
        or not specificity
        or not all(isinstance(item, str) and item.strip() for item in specificity)
        or len(set(specificity)) != len(specificity)
    ):
        raise ValueError("priority.specificity_keys must be unique non-empty strings")

    trust = _mapping(data, "trust")
    _string_list(trust, "source_profile_files")
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
        raise ValueError(f"trust.matrix missing profiles: {', '.join(missing_profiles)}")
    for profile, stake_rules in matrix.items():
        rules = _mapping_value(stake_rules, f"trust.matrix.{profile}")
        for stake in ("low", "med", "high", "critical"):
            rule = _mapping(rules, stake)
            mode = rule.get("mode")
            if mode not in {"svatah", "paratah", "yogyata", "quarantine", "reject"}:
                raise ValueError(f"trust.matrix.{profile}.{stake}.mode is invalid")
            k = _bounded_int(rule, "k", 0, 20)
            method = rule.get("method")
            if method not in {None, "cross_source", "tool_recheck", "chain_audit", "human"}:
                raise ValueError(f"trust.matrix.{profile}.{stake}.method is invalid")
            if mode in {"paratah", "quarantine"} and (k < 1 or method is None):
                raise ValueError(f"trust.matrix.{profile}.{stake} requires k and method")
            if mode in {"svatah", "yogyata", "reject"} and (k != 0 or method is not None):
                raise ValueError(f"trust.matrix.{profile}.{stake} must use k=0 and method=null")
    yogyata = _mapping(trust, "yogyata")
    _bounded_number(yogyata, "min_coverage", 0.0, 1.0)
    _bounded_number(yogyata, "min_recall", 0.0, 1.0)
    apta = _mapping(trust, "apta")
    _bounded_int(apta, "minimum_samples", 0, 1_000_000)
    for key in ("alpha_prior", "beta_prior"):
        value = apta.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"trust.apta.{key} must be positive")
    floor = _number(apta, "floor")
    ceiling = _number(apta, "ceiling")
    if not 0 <= floor <= ceiling <= 1:
        raise ValueError("trust.apta must satisfy 0 <= floor <= ceiling <= 1")

    ttl = _mapping(data, "perishability_ttl")
    for key in ("stable_seconds", "slow_seconds", "fast_seconds", "live_seconds"):
        value = ttl.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"perishability_ttl.{key} must be null or a non-negative integer")
    _bounded_int(_mapping(data, "engine"), "max_relabel_iterations", 1, 10_000)


def _mutable_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, FrozenList)):
        return [_mutable_copy(item) for item in value]
    return value


def _unknown_paths(
    value: Mapping[str, Any], defaults: Mapping[str, Any], prefix: str = ""
) -> list[str]:
    unknown: list[str] = []
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in defaults:
            unknown.append(path)
        elif isinstance(item, Mapping) and isinstance(defaults[key], Mapping):
            unknown.extend(_unknown_paths(item, defaults[key], path))
    return sorted(unknown)


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise ValueError(f"{key} must be a mapping")
    return result


def _mapping_value(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _string_list(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    items = value.get(key)
    if (
        not isinstance(items, list)
        or len(items) > 20
        or not all(isinstance(item, str) and item.strip() for item in items)
    ):
        raise ValueError(f"{key} must be a list of at most 20 non-empty paths")
    return tuple(items)


def _rank_mapping(value: Any, label: str) -> dict[str, int]:
    mapping = _mapping_value(value, label)
    if not mapping or not all(
        isinstance(key, str)
        and key.strip()
        and isinstance(rank, int)
        and not isinstance(rank, bool)
        for key, rank in mapping.items()
    ):
        raise ValueError(f"{label} must contain non-empty integer ranks")
    return {str(key): int(rank) for key, rank in mapping.items()}


def _boolean(value: Mapping[str, Any], key: str) -> None:
    if not isinstance(value.get(key), bool):
        raise ValueError(f"{key} must be a boolean")


def _bounded_int(value: Mapping[str, Any], key: str, minimum: int, maximum: int) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
        raise ValueError(f"{key} must be an integer in [{minimum},{maximum}]")
    return item


def _number(value: Mapping[str, Any], key: str) -> float:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
        raise ValueError(f"{key} must be a finite number")
    return float(item)


def _bounded_number(value: Mapping[str, Any], key: str, minimum: float, maximum: float) -> float:
    item = _number(value, key)
    if not minimum <= item <= maximum:
        raise ValueError(f"{key} must be in [{minimum},{maximum}]")
    return item
