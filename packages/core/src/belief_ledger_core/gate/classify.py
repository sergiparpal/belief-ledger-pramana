"""Versioned action-policy registry and conservative unknown classification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..models import Stakes

_MUTATION_WORDS = re.compile(
    r"\b(write|delete|remove|send|publish|execute|deploy|approve|purchase|install|enable|disable|update|create|submit|upload|patch|mutat|commit|push)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    id: str
    base_stakes: Stakes
    effectful: bool
    minimum_priority: str
    allow_human_approval: bool
    target_fields: tuple[str, ...]
    preconditions: tuple[str, ...]
    exact: tuple[str, ...] = ()
    pattern: str | None = None


@dataclass(frozen=True, slots=True)
class ActionClassification:
    policy: ActionPolicy
    known: bool
    reason: str


class ActionPolicyRegistry:
    def __init__(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("rules"), list):
            raise ValueError("action policy registry schema is invalid")
        self.rules = tuple(_parse_rule(item) for item in data["rules"])

    def classify(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        description: str = "",
        enforce: bool = True,
        unknown_tool_policy: str = "conservative",
    ) -> ActionClassification:
        name = tool_name.casefold().strip()
        for rule in self.rules:
            if name in rule.exact:
                return self._terminal_adjust(rule, args)
        for rule in self.rules:
            if rule.pattern and re.fullmatch(rule.pattern, name):
                return self._terminal_adjust(rule, args)
        material = f"{name} {description} {' '.join(str(key) for key in args)}"
        if _MUTATION_WORDS.search(material):
            return ActionClassification(
                _unknown_policy(Stakes.HIGH, True), False, "unknown tool appears effectful"
            )
        stakes = Stakes.HIGH if enforce else Stakes.MED
        return ActionClassification(
            _unknown_policy(stakes, enforce),
            False,
            (
                "unknown tools require an exact read-only policy entry; "
                "name-based read-only inference is disabled"
                if unknown_tool_policy == "allow_read_only"
                else "unknown tool is conservatively effectful"
            ),
        )

    def _terminal_adjust(self, rule: ActionPolicy, args: dict[str, Any]) -> ActionClassification:
        if rule.id != "terminal":
            return ActionClassification(rule, True, f"matched policy {rule.id}")
        # A command string is interpreted by the host-selected shell.  The
        # plugin does not receive a shell dialect or an argv vector, so it
        # cannot prove that even a familiar command name is observational on
        # every supported backend.  Treat every terminal invocation as an
        # effectful action and leave any read-only optimization to a host API
        # that supplies structured argv plus a non-shell execution guarantee.
        del args
        return ActionClassification(
            rule,
            True,
            "terminal command is conservatively effectful across shell backends",
        )


def _parse_rule(value: Any) -> ActionPolicy:
    if not isinstance(value, dict):
        raise ValueError("action policy rule must be a mapping")
    pattern = value.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise ValueError(f"action policy {value.get('id')} regex must be a string")
        if not pattern.startswith("^") or not pattern.endswith("$"):
            raise ValueError(f"action policy {value.get('id')} regex must be anchored")
        re.compile(pattern)
    required = {
        "id",
        "base_stakes",
        "effectful",
        "minimum_priority",
        "allow_human_approval",
        "target_fields",
        "preconditions",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"action policy rule is missing required fields: {', '.join(missing)}")
    identifier = value["id"]
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("action policy id must be a non-empty string")
    if not isinstance(value["effectful"], bool):
        raise ValueError(f"action policy {identifier} effectful must be a boolean")
    if not isinstance(value["allow_human_approval"], bool):
        raise ValueError(f"action policy {identifier} allow_human_approval must be a boolean")
    minimum_priority = value["minimum_priority"]
    if minimum_priority not in {"untrusted", "semi", "trusted"}:
        raise ValueError(f"action policy {identifier} minimum_priority is invalid")
    target_fields = _string_list(value["target_fields"], "target_fields", identifier)
    preconditions = _string_list(value["preconditions"], "preconditions", identifier)
    exact = _string_list(value.get("exact", []), "exact", identifier)
    return ActionPolicy(
        id=identifier,
        base_stakes=Stakes(str(value["base_stakes"])),
        effectful=value["effectful"],
        minimum_priority=minimum_priority,
        allow_human_approval=value["allow_human_approval"],
        target_fields=target_fields,
        preconditions=preconditions,
        exact=tuple(item.casefold() for item in exact),
        pattern=pattern,
    )


def _string_list(value: Any, field: str, identifier: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"action policy {identifier} {field} must be a list of non-empty strings")
    return tuple(value)


def _unknown_policy(stakes: Stakes, effectful: bool) -> ActionPolicy:
    return ActionPolicy(
        "unknown",
        stakes,
        effectful,
        "trusted" if effectful else "untrusted",
        False,
        (),
        ("operator_policy",) if effectful else (),
    )
