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
_READ_WORDS = re.compile(
    r"\b(read|get|list|search|find|query|inspect|view|fetch|stat|show)\b", re.IGNORECASE
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
        if unknown_tool_policy == "allow_read_only" and _READ_WORDS.search(material):
            return ActionClassification(
                _unknown_policy(Stakes.MED, False), False, "unknown tool appears read-only"
            )
        stakes = Stakes.HIGH if enforce else Stakes.MED
        return ActionClassification(
            _unknown_policy(stakes, enforce),
            False,
            "unknown tool is ambiguous"
            if unknown_tool_policy == "allow_read_only"
            else "unknown tool is conservatively effectful",
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
        pattern = str(pattern)
        if not pattern.startswith("^") or not pattern.endswith("$"):
            raise ValueError(f"action policy {value.get('id')} regex must be anchored")
        re.compile(pattern)
    return ActionPolicy(
        id=str(value["id"]),
        base_stakes=Stakes(str(value["base_stakes"])),
        effectful=bool(value["effectful"]),
        minimum_priority=str(value["minimum_priority"]),
        allow_human_approval=bool(value["allow_human_approval"]),
        target_fields=tuple(str(item) for item in value.get("target_fields", [])),
        preconditions=tuple(str(item) for item in value.get("preconditions", [])),
        exact=tuple(str(item).casefold() for item in value.get("exact", [])),
        pattern=pattern,
    )


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
