"""Host-neutral configuration loader with an explicit state root."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .events import canonical_json, content_hash


@dataclass(frozen=True, slots=True)
class CoreConfigSnapshot:
    schema_version: int
    state_root: Path
    source: Path | None
    data: dict[str, Any]
    digest: str


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
    return CoreConfigSnapshot(1, root, source, data, content_hash(canonical_json(data)))


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
