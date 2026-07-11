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
    args: dict[str, Any],
    target_fields: tuple[str, ...],
    beliefs: list[Belief],
    sources: Mapping[str, Source],
    conflicts: list[Conflict],
    minimum_integrity: str,
) -> tuple[PreconditionResult, ...]:
    target = _target(args, target_fields)
    results: list[PreconditionResult] = []
    for name in names:
        if name == "no_open_conflict":
            satisfied = not conflicts
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
            _confirmation_belief(target, beliefs, sources, minimum_integrity)
            if name == "explicit_user_confirmation"
            else _entailing_belief(proposition, beliefs, sources, minimum_integrity)
        )
        results.append(
            PreconditionResult(
                name,
                proposition,
                match is not None,
                match.id if match else None,
                "supported by an IN belief" if match else "no qualifying IN belief",
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
    wanted = set(normalize_content(proposition).split())
    for belief in sorted(beliefs, key=lambda item: item.id):
        if belief.status is not Status.IN or ranks[sources[belief.source_id].integrity] < required:
            continue
        actual = set(belief.normalized_content.split())
        if wanted <= actual or belief.normalized_content == normalize_content(proposition):
            return belief
    return None


def _confirmation_belief(
    target: str,
    beliefs: list[Belief],
    sources: Mapping[str, Source],
    minimum_integrity: str,
) -> Belief | None:
    target_tokens = set(normalize_content(target).split())
    required = {"untrusted": 0, "semi": 1, "trusted": 2}.get(minimum_integrity, 2)
    ranks = {Integrity.UNTRUSTED: 0, Integrity.SEMI: 1, Integrity.TRUSTED: 2}
    for belief in sorted(beliefs, key=lambda item: item.id):
        source = sources[belief.source_id]
        if (
            belief.status is not Status.IN
            or source.kind.value != "user"
            or ranks[source.integrity] < required
        ):
            continue
        content = belief.normalized_content
        if not any(token in content for token in ("confirm", "authoriz", "approve", "autoriz")):
            continue
        if not target_tokens or target_tokens <= set(content.split()):
            return belief
    return None
