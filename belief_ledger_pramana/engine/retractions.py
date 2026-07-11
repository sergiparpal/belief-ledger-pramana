"""Structural retraction helpers."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Justification
from .graph import descendants


def affected_subgraph(
    justifications: Iterable[Justification], defeated_root: str
) -> tuple[str, ...]:
    return descendants(justifications, defeated_root)


def notice_expired(created_turn: int, ttl_turns: int, current_turn: int) -> bool:
    return current_turn - created_turn >= ttl_turns
