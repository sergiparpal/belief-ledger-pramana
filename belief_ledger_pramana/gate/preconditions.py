"""Deterministic common action-precondition resolution."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

from ..engine.validity import normalize_content
from ..models import Belief, Conflict, Integrity, Source, Status

_AFFIRMATIVE_CONFIRMATION = re.compile(
    r"^(?:i|we|yo)\s+(?:hereby\s+)?(?:confirm\w*|authoriz\w*|approve\w*|autoriz\w*)\b"
)


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
            _confirmation_belief(
                target,
                action_name,
                beliefs,
                sources,
                minimum_integrity,
                confirmation_ttl_seconds=confirmation_ttl_seconds,
            )
            if name == "explicit_user_confirmation"
            else _entailing_belief(
                proposition,
                beliefs,
                sources,
                minimum_integrity,
                exact=name == "resource_identity",
            )
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
    *,
    exact: bool = False,
) -> Belief | None:
    ranks = {Integrity.UNTRUSTED: 0, Integrity.SEMI: 1, Integrity.TRUSTED: 2}
    required = {"untrusted": 0, "semi": 1, "trusted": 2}.get(minimum_integrity, 2)
    wanted = set(normalize_content(proposition).split())
    for belief in sorted(beliefs, key=lambda item: item.id):
        if belief.status is not Status.IN or ranks[sources[belief.source_id].integrity] < required:
            continue
        if _contains_negation(belief.normalized_content):
            continue
        actual = set(belief.normalized_content.split())
        normalized = normalize_content(proposition)
        if belief.normalized_content == normalized:
            return belief
        if not exact and wanted <= actual:
            return belief
    return None


def _confirmation_belief(
    target: str,
    action_name: str,
    beliefs: list[Belief],
    sources: Mapping[str, Source],
    minimum_integrity: str,
    *,
    confirmation_ttl_seconds: int,
) -> Belief | None:
    target_tokens = set(normalize_content(target).split())
    required = {"untrusted": 0, "semi": 1, "trusted": 2}.get(minimum_integrity, 2)
    ranks = {Integrity.UNTRUSTED: 0, Integrity.SEMI: 1, Integrity.TRUSTED: 2}
    now = datetime.now(UTC)
    for belief in sorted(beliefs, key=lambda item: item.id):
        source = sources[belief.source_id]
        if (
            belief.status is not Status.IN
            or source.kind.value != "user"
            or ranks[source.integrity] < required
        ):
            continue
        age_seconds = (
            (now - belief.observed_at).total_seconds()
            if belief.observed_at.tzinfo is not None
            else float("inf")
        )
        if age_seconds < 0 or age_seconds > confirmation_ttl_seconds:
            continue
        content = belief.normalized_content
        tokens = set(re.findall(r"[\w.-]+", content))
        if _contains_negation(content):
            continue
        if _AFFIRMATIVE_CONFIRMATION.search(content) is None:
            continue
        action_tokens = set(re.findall(r"[\w.-]+", action_name.replace("_", " ")))
        if target_tokens <= tokens and action_tokens <= tokens:
            return belief
    return None


def _contains_negation(content: str) -> bool:
    return bool(
        re.search(
            r"\b(?:not|no|never|without|deny|denied|decline|declined|don't|do not|cannot|can't|unconfirm)\b",
            content,
        )
    )
