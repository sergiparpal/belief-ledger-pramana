"""Deterministic JTMS-style fixed-point relabeling with saṃśaya."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import (
    Belief,
    DefeatEdge,
    DefeatKind,
    IngestionSupport,
    Justification,
    Pramana,
    Source,
    Status,
)
from .priority import compare_priority

_BASIC_TYPES = {Pramana.PRATYAKSHA, Pramana.SHABDA, Pramana.ANUPALABDHI}


@dataclass(frozen=True, slots=True)
class RelabelResult:
    statuses: dict[str, Status]
    active_edges: dict[str, bool]
    conflicts: tuple[tuple[str, str], ...]
    causes: dict[str, str]
    iterations: int
    oscillation: bool


def relabel(
    beliefs: Mapping[str, Belief],
    justifications: Iterable[Justification],
    supports: Iterable[IngestionSupport],
    defeats: Iterable[DefeatEdge],
    sources: Mapping[str, Source],
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> RelabelResult:
    """Relabel the finite affected graph until a deterministic fixed point."""

    ordered_ids = tuple(sorted(beliefs))
    justification_list = tuple(sorted(justifications, key=lambda item: item.id))
    support_list = tuple(sorted(supports, key=lambda item: item.id))
    defeat_list = tuple(sorted(defeats, key=lambda item: item.id))
    by_belief: dict[str, list[Justification]] = defaultdict(list)
    for justification in justification_list:
        by_belief[justification.belief_id].append(justification)
    supports_by_belief: dict[str, list[IngestionSupport]] = defaultdict(list)
    for support in support_list:
        supports_by_belief[support.belief_id].append(support)
    target_owners = {support.id: support.belief_id for support in support_list} | {
        justification.id: justification.belief_id for justification in justification_list
    }
    undercuts: dict[str, list[DefeatEdge]] = defaultdict(list)
    rebuts: list[DefeatEdge] = []
    for edge in defeat_list:
        if edge.kind is DefeatKind.UNDERCUT:
            undercuts[edge.target].append(edge)
        else:
            rebuts.append(edge)

    current = {belief_id: beliefs[belief_id].status for belief_id in ordered_ids}
    seen: set[tuple[str, ...]] = set()
    max_iterations = int(config.get("engine", {}).get("max_relabel_iterations", 256))
    conflict_pairs: set[tuple[str, str]] = set()
    causes: dict[str, str] = {}
    rebut_comparisons: dict[str, Any] = {}
    for edge in rebuts:
        attacker = beliefs.get(edge.attacker)
        target = beliefs.get(edge.target)
        if attacker is not None and target is not None:
            rebut_comparisons[edge.id] = compare_priority(attacker, target, sources, config)

    for iteration in range(1, max_iterations + 1):
        state_key = tuple(current[item].value for item in ordered_ids)
        seen.add(state_key)
        supported = {
            belief_id: _is_supported(
                beliefs[belief_id],
                current,
                by_belief,
                supports_by_belief,
                undercuts,
            )
            for belief_id in ordered_ids
        }
        next_status = {
            belief_id: _candidate_status(beliefs[belief_id], supported[belief_id])
            for belief_id in ordered_ids
        }
        if now is not None:
            for belief_id in ordered_ids:
                belief = beliefs[belief_id]
                if _is_stale(belief, config, now) and next_status[belief_id] is Status.IN:
                    next_status[belief_id] = Status.PENDING
                    causes[belief_id] = f"stale:{belief.perishability.value}"
        conflict_pairs = set()

        # Equal-priority contradictions are persistent open conflicts, independent
        # of whether the prior iteration already marked either side PENDING.
        for edge in rebuts:
            attacker = beliefs.get(edge.attacker)
            target = beliefs.get(edge.target)
            if attacker is None or target is None:
                continue
            comparison = rebut_comparisons.get(edge.id)
            if comparison is None:
                continue
            if (
                comparison.result == 0
                and supported[attacker.id]
                and supported[target.id]
                and attacker.admission_status not in {Status.OUT, Status.QUARANTINED}
                and target.admission_status not in {Status.OUT, Status.QUARANTINED}
            ):
                pair = (
                    (attacker.id, target.id)
                    if attacker.id <= target.id
                    else (target.id, attacker.id)
                )
                conflict_pairs.add(pair)
                next_status[attacker.id] = Status.PENDING
                next_status[target.id] = Status.PENDING
                causes[attacker.id] = f"samsaya:{target.id}"
                causes[target.id] = f"samsaya:{attacker.id}"

        # A winning rebut is live only from an attacker that was IN in this
        # iteration. Re-running supplies reinstatement when that attacker falls.
        for edge in rebuts:
            attacker = beliefs.get(edge.attacker)
            target = beliefs.get(edge.target)
            if attacker is None or target is None or (attacker.id, target.id) in conflict_pairs:
                continue
            pair = (
                (attacker.id, target.id) if attacker.id <= target.id else (target.id, attacker.id)
            )
            if pair in conflict_pairs:
                continue
            comparison = rebut_comparisons.get(edge.id)
            if comparison is None:
                continue
            if comparison.result > 0 and current[attacker.id] is Status.IN and supported[target.id]:
                next_status[target.id] = Status.OUT
                causes[target.id] = f"rebut:{attacker.id}:{comparison.decisive_field}"

        for belief_id in ordered_ids:
            if not supported[belief_id]:
                causes[belief_id] = _support_loss_cause(
                    beliefs[belief_id], current, by_belief, supports_by_belief, undercuts
                )
            elif next_status[belief_id] is Status.IN and current[belief_id] is Status.OUT:
                causes[belief_id] = "reinstated:live_support_no_winning_attacker"

        next_key = tuple(next_status[item].value for item in ordered_ids)
        if next_key == state_key:
            active = _active_edges(
                defeat_list, beliefs, next_status, sources, config, rebut_comparisons
            )
            return RelabelResult(
                next_status, active, tuple(sorted(conflict_pairs)), causes, iteration, False
            )
        if next_key in seen:
            involved = _defeat_cycle_nodes(defeat_list, target_owners=target_owners)
            for belief_id in involved:
                if belief_id in next_status and supported.get(belief_id):
                    next_status[belief_id] = Status.PENDING
                    causes[belief_id] = "samsaya:defeat_cycle"
            active = _active_edges(
                defeat_list, beliefs, next_status, sources, config, rebut_comparisons
            )
            return RelabelResult(
                next_status, active, tuple(sorted(conflict_pairs)), causes, iteration, True
            )
        current = next_status

    supported = {
        belief_id: _is_supported(
            beliefs[belief_id], current, by_belief, supports_by_belief, undercuts
        )
        for belief_id in ordered_ids
    }
    for belief_id in ordered_ids:
        if not supported[belief_id]:
            current[belief_id] = _candidate_status(beliefs[belief_id], False)
            causes[belief_id] = _support_loss_cause(
                beliefs[belief_id], current, by_belief, supports_by_belief, undercuts
            )
    involved = _defeat_cycle_nodes(defeat_list, target_owners=target_owners) or set(ordered_ids)
    for belief_id in involved:
        if supported.get(belief_id):
            current[belief_id] = Status.PENDING
            causes[belief_id] = "samsaya:iteration_ceiling"
    active = _active_edges(defeat_list, beliefs, current, sources, config, rebut_comparisons)
    return RelabelResult(
        current, active, tuple(sorted(conflict_pairs)), causes, max_iterations, True
    )


def _candidate_status(belief: Belief, supported: bool) -> Status:
    if belief.admission_status is Status.QUARANTINED:
        return Status.QUARANTINED
    if belief.admission_status is Status.OUT or not supported:
        return Status.OUT
    if belief.admission_status is Status.PENDING:
        return Status.PENDING
    return Status.IN


def _is_stale(belief: Belief, config: dict[str, Any], now: datetime) -> bool:
    """Return whether a non-stable belief has exceeded its configured lifetime."""

    ttl = config.get("perishability_ttl", {}).get(f"{belief.perishability.value}_seconds")
    if ttl is None:
        return False
    try:
        seconds = int(ttl)
    except (TypeError, ValueError):
        return True
    if seconds < 0 or belief.observed_at.tzinfo is None or now.tzinfo is None:
        return True
    return (now - belief.observed_at).total_seconds() > seconds


def _is_supported(
    belief: Belief,
    statuses: Mapping[str, Status],
    by_belief: Mapping[str, list[Justification]],
    supports_by_belief: Mapping[str, list[IngestionSupport]],
    undercuts: Mapping[str, list[DefeatEdge]],
) -> bool:
    if belief.pramana in _BASIC_TYPES:
        return any(
            support.active and not _has_active_undercut(support.id, statuses, undercuts)
            for support in supports_by_belief.get(belief.id, ())
        )
    return any(
        all(statuses.get(premise) is Status.IN for premise in justification.premises)
        and not _has_active_undercut(justification.id, statuses, undercuts)
        for justification in by_belief.get(belief.id, ())
    )


def _has_active_undercut(
    target: str,
    statuses: Mapping[str, Status],
    undercuts: Mapping[str, list[DefeatEdge]],
) -> bool:
    return any(statuses.get(edge.attacker) is Status.IN for edge in undercuts.get(target, ()))


def _support_loss_cause(
    belief: Belief,
    statuses: Mapping[str, Status],
    by_belief: Mapping[str, list[Justification]],
    supports_by_belief: Mapping[str, list[IngestionSupport]],
    undercuts: Mapping[str, list[DefeatEdge]],
) -> str:
    if belief.pramana in _BASIC_TYPES:
        for support in supports_by_belief.get(belief.id, ()):
            for edge in undercuts.get(support.id, ()):
                if statuses.get(edge.attacker) is Status.IN:
                    return f"undercut:{edge.attacker}:{support.id}"
        return "invalid_or_missing_ingestion_support"
    fallen = sorted(
        premise
        for justification in by_belief.get(belief.id, ())
        for premise in justification.premises
        if statuses.get(premise) is not Status.IN
    )
    if fallen:
        return "premise_out:" + ",".join(fallen)
    for justification in by_belief.get(belief.id, ()):
        for edge in undercuts.get(justification.id, ()):
            if statuses.get(edge.attacker) is Status.IN:
                return f"undercut:{edge.attacker}:{justification.id}"
    return "no_live_justification"


def _active_edges(
    defeats: Iterable[DefeatEdge],
    beliefs: Mapping[str, Belief],
    statuses: Mapping[str, Status],
    sources: Mapping[str, Source],
    config: dict[str, Any],
    rebut_comparisons: Mapping[str, Any] | None = None,
) -> dict[str, bool]:
    active: dict[str, bool] = {}
    for edge in defeats:
        if edge.kind is DefeatKind.UNDERCUT:
            active[edge.id] = statuses.get(edge.attacker) is Status.IN
            continue
        attacker = beliefs.get(edge.attacker)
        target = beliefs.get(edge.target)
        if attacker is None or target is None:
            active[edge.id] = False
            continue
        comparison = (
            rebut_comparisons.get(edge.id)
            if rebut_comparisons is not None
            else compare_priority(attacker, target, sources, config)
        )
        active[edge.id] = bool(
            statuses.get(edge.attacker) is Status.IN
            and comparison is not None
            and comparison.result > 0
        )
    return active


def _defeat_cycle_nodes(
    defeats: Iterable[DefeatEdge], *, target_owners: Mapping[str, str] | None = None
) -> set[str]:
    graph: dict[str, set[str]] = defaultdict(set)
    owners = target_owners or {}
    for edge in defeats:
        if edge.kind is DefeatKind.REBUT:
            graph[edge.attacker].add(edge.target)
        elif edge.target in owners:
            graph[edge.attacker].add(owners[edge.target])
    nodes = set(graph)
    for children in graph.values():
        nodes.update(children)
    visited: set[str] = set()
    finish_order: list[str] = []
    for root in sorted(nodes):
        if root in visited:
            continue
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finish_order.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            stack.extend(
                (child, False)
                for child in sorted(graph.get(node, ()), reverse=True)
                if child not in visited
            )

    reverse: dict[str, set[str]] = defaultdict(set)
    for parent, children in graph.items():
        for child in children:
            reverse[child].add(parent)
    cycle_nodes: set[str] = set()
    assigned: set[str] = set()
    for root in reversed(finish_order):
        if root in assigned:
            continue
        component: set[str] = set()
        component_stack = [root]
        assigned.add(root)
        while component_stack:
            node = component_stack.pop()
            component.add(node)
            for parent in sorted(reverse.get(node, ()), reverse=True):
                if parent not in assigned:
                    assigned.add(parent)
                    component_stack.append(parent)
        if len(component) > 1 or root in graph.get(root, ()):
            cycle_nodes.update(component)
    return cycle_nodes
