"""Host-neutral configuration loader with an explicit state root."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .events import canonical_json, content_hash


class FrozenDict(dict[str, Any]):
    """Recursively immutable dict retaining dict compatibility for policy code."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("configuration snapshots are immutable")

    __delitem__ = _immutable
    __ior__ = _immutable  # type: ignore[assignment]
    __setitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable  # type: ignore[assignment]
    setdefault = _immutable
    update = _immutable

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        return copy.deepcopy(dict(self), memo)


class FrozenList(list[Any]):
    """Recursively immutable list retaining list compatibility for consumers."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("configuration snapshots are immutable")

    __delitem__ = _immutable
    __iadd__ = _immutable  # type: ignore[assignment]
    __imul__ = _immutable  # type: ignore[assignment]
    __setitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        return copy.deepcopy(list(self), memo)


def freeze_config(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenDict({str(key): freeze_config(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze_config(item) for item in value)
    return copy.deepcopy(value)


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
    return CoreConfigSnapshot(1, root, source, data, content_hash(canonical_json(data)))


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
