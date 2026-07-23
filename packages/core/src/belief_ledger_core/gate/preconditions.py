"""Deterministic common action-precondition resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from ..engine.validity import normalize_content
from ..models import Belief, Conflict, Integrity, Source, Status


@dataclass(frozen=True, slots=True)
class PreconditionResult:
    name: str
    proposition: str
    satisfied: bool
    belief_id: str | None
    reason: str
    suggestion: str


def resolve_preconditions(
    names: tuple[str, ...],
    *,
    action_name: str,
    args: dict[str, Any],
    target_fields: tuple[str, ...],
    beliefs: list[Belief],
    sources: Mapping[str, Source],
    conflicts: list[Conflict],
    minimum_integrity: str,
    confirmation_ttl_seconds: int = 300,
) -> tuple[PreconditionResult, ...]:
    target = _target(args, target_fields)
    results: list[PreconditionResult] = []
    for name in names:
        if name == "no_open_conflict":
            relevant_conflicts = [
                conflict
                for conflict in conflicts
                if _conflict_affects_target(conflict, target, beliefs)
            ]
            satisfied = not relevant_conflicts
            results.append(
                PreconditionResult(
                    name,
                    "No open ledger conflict affects this action",
                    satisfied,
                    None,
                    "no open conflicts" if satisfied else "ledger has an open conflict",
                    "Resolve the listed conflict with a read-only observation",
                )
            )
            continue
        proposition, suggestion = _proposition(name, target)
        match = (
            None
            if name == "explicit_user_confirmation"
            else _entailing_belief(
                proposition,
                beliefs,
                sources,
                minimum_integrity,
            )
        )
        results.append(
            PreconditionResult(
                name,
                proposition,
                match is not None,
                match.id if match else None,
                (
                    "host-bound approval is required"
                    if name == "explicit_user_confirmation"
                    else "supported by an exact IN belief"
                    if match
                    else "no qualifying exact IN belief"
                ),
                suggestion,
            )
        )
    return tuple(results)


def _target(args: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = args.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "the requested target"


def _proposition(name: str, target: str) -> tuple[str, str]:
    if name == "target_exists":
        return f"Target {target} exists", f"Use a read-only stat/list operation on {target}"
    if name == "parent_exists":
        parent = str(PurePath(target).parent)
        return f"Parent {parent} exists", f"Use a read-only stat/list operation on {parent}"
    if name == "environment_known":
        return (
            "The current execution environment is identified",
            "Use a read-only environment identity command",
        )
    if name == "resource_identity":
        return (
            f"Resource {target} is the intended target",
            f"Read the identity of {target} without mutating it",
        )
    if name == "explicit_user_confirmation":
        return (
            f"The user explicitly confirmed the action on {target}",
            "Ask the user for explicit confirmation",
        )
    if name == "operator_policy":
        return (
            "The operator configured a policy for this tool",
            "Add an anchored action-policy rule and retry",
        )
    if name == "version_freshness":
        return f"The version of {target} is current", f"Use a read-only version query for {target}"
    return f"Precondition {name} holds for {target}", f"Obtain a read-only observation for {name}"


def _entailing_belief(
    proposition: str,
    beliefs: list[Belief],
    sources: Mapping[str, Source],
    minimum_integrity: str,
) -> Belief | None:
    ranks = {Integrity.UNTRUSTED: 0, Integrity.SEMI: 1, Integrity.TRUSTED: 2}
    required = {"untrusted": 0, "semi": 1, "trusted": 2}.get(minimum_integrity, 2)
    normalized = normalize_content(proposition)
    for belief in sorted(beliefs, key=lambda item: item.id):
        if belief.status is not Status.IN or ranks[sources[belief.source_id].integrity] < required:
            continue
        if _contains_negation(belief.normalized_content):
            continue
        if belief.normalized_content == normalized:
            return belief
    return None


def _contains_negation(content: str) -> bool:
    tokens = set(normalize_content(content).replace("'", "").split())
    return bool(
        tokens
        & {
            "not",
            "no",
            "never",
            "without",
            "deny",
            "denied",
            "decline",
            "declined",
            "dont",
            "cannot",
            "cant",
            "unconfirm",
        }
    )


def _conflict_affects_target(conflict: Conflict, target: str, beliefs: list[Belief]) -> bool:
    target_tokens = set(normalize_content(target).split())
    if not target_tokens or target == "the requested target":
        return True
    by_id = {belief.id: belief for belief in beliefs}
    conflict_tokens: set[str] = set()
    for value in conflict.normalized_scope.values():
        conflict_tokens.update(normalize_content(value).split())
    for belief_id in (conflict.left_belief_id, conflict.right_belief_id):
        belief = by_id.get(belief_id)
        if belief is not None:
            conflict_tokens.update(belief.normalized_content.split())
            for value in belief.qualifiers.values():
                conflict_tokens.update(normalize_content(value).split())
    return bool(target_tokens & conflict_tokens)
