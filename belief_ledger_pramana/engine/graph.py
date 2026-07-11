"""Acyclic justification graph helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from ..models import Justification


def adjacency(justifications: Iterable[Justification]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for justification in justifications:
        for premise in justification.premises:
            graph[premise].add(justification.belief_id)
    return dict(graph)


def cycle_path(
    justifications: Iterable[Justification], conclusion: str, premises: Iterable[str]
) -> tuple[str, ...] | None:
    """Return the exact path closed by a proposed premise→conclusion edge."""

    graph = adjacency(justifications)
    for premise in premises:
        if premise == conclusion:
            return (conclusion, conclusion)
        path = _find_path(graph, conclusion, premise)
        if path:
            return tuple([*path, conclusion])
    return None


def descendants(justifications: Iterable[Justification], root: str) -> tuple[str, ...]:
    graph = adjacency(justifications)
    seen: set[str] = set()
    queue = deque(sorted(graph.get(root, ())))
    while queue:
        item = queue.popleft()
        if item in seen:
            continue
        seen.add(item)
        queue.extend(sorted(graph.get(item, ())))
    return tuple(sorted(seen))


def _find_path(graph: dict[str, set[str]], start: str, goal: str) -> list[str] | None:
    stack: list[tuple[str, list[str]]] = [(start, [start])]
    visited: set[str] = set()
    while stack:
        node, path = stack.pop()
        if node == goal:
            return path
        if node in visited:
            continue
        visited.add(node)
        for child in sorted(graph.get(node, ()), reverse=True):
            stack.append((child, [*path, child]))
    return None
