"""Small recursively immutable JSON-shaped value containers."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any, TypeVar, overload

_T = TypeVar("_T")


class FrozenDict(Mapping[str, Any]):
    """A mapping that cannot be mutated, including through base-class escape hatches."""

    __slots__ = ("_data",)

    def __init__(
        self,
        value: Mapping[str, Any] | Iterable[tuple[str, Any]] = (),
    ) -> None:
        self._data = dict(value)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FrozenDict({self._data!r})"

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        return copy.deepcopy(self._data, memo)


class FrozenList(Sequence[_T]):
    """A list-compatible read-only sequence backed by a tuple."""

    __slots__ = ("_items",)

    def __init__(self, value: Iterable[_T] = ()) -> None:
        self._items = tuple(value)

    @overload
    def __getitem__(self, index: int) -> _T: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[_T]: ...

    def __getitem__(self, index: int | slice) -> _T | Sequence[_T]:
        return self._items[index]

    def __iter__(self) -> Iterator[_T]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"FrozenList({self._items!r})"

    def __deepcopy__(self, memo: dict[int, Any]) -> list[_T]:
        return copy.deepcopy(list(self._items), memo)


def freeze(value: Any) -> Any:
    """Recursively freeze mappings and lists while copying scalar values."""

    if isinstance(value, Mapping):
        return FrozenDict((str(key), freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return FrozenList(freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    return copy.deepcopy(value)
