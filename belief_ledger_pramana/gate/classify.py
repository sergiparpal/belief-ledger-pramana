"""Versioned action-policy registry and conservative unknown classification."""

from __future__ import annotations

import re
import shlex
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
# A terminal command must prove that it is read-only; matching a harmless first
# word is not sufficient.  In particular, shell composition turns otherwise
# safe-looking commands into arbitrary execution.
_SHELL_SYNTAX = re.compile(r"[;&|`$<>\n\r]")
_FIND_MUTATING_ACTION = re.compile(
    r"^-(?:delete|exec(?:dir)?|ok(?:dir)?|fls|fprint(?:f|0)?|fprintf)$"
)
_RG_EXECUTION_OPTION = re.compile(r"^--pre(?:=|$)")
_GIT_EXECUTION_OPTION = re.compile(r"^--(?:ext-diff|textconv|paginate)(?:=|$)")
_SAFE_GIT_SUBCOMMANDS = frozenset(
    {
        "blame",
        "branch",
        "describe",
        "diff",
        "grep",
        "log",
        "ls-files",
        "remote",
        "rev-parse",
        "show",
        "status",
        "tag",
        "version",
    }
)
_SIMPLE_READ_COMMANDS = frozenset({"pwd", "ls", "rg", "grep", "cat", "head", "tail", "stat", "wc"})


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
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if not command:
            return ActionClassification(
                rule, True, "terminal command is missing and conservatively effectful"
            )
        if _SHELL_SYNTAX.search(command):
            return ActionClassification(
                rule,
                True,
                "terminal command uses shell composition and is conservatively effectful",
            )
        try:
            tokens = shlex.split(command)
        except ValueError:
            return ActionClassification(rule, True, "terminal command could not be parsed")
        if _is_strictly_read_only_command(tokens):
            read_rule = ActionPolicy(
                "terminal_read_only",
                Stakes.MED,
                False,
                "untrusted",
                False,
                rule.target_fields,
                (),
                exact=rule.exact,
            )
            return ActionClassification(
                read_rule, True, "terminal command matches a read-only primitive"
            )
        return ActionClassification(rule, True, "terminal command is conservatively effectful")


def _is_strictly_read_only_command(tokens: list[str]) -> bool:
    """Recognize a deliberately small terminal read-only grammar.

    This is intentionally a proof obligation rather than a blacklist.  New
    commands stay effectful until an operator adds a policy or this grammar is
    extended with tests for their argument semantics.
    """

    if not tokens:
        return False
    command = tokens[0]
    if command in _SIMPLE_READ_COMMANDS:
        # rg can delegate to an arbitrary executable through --pre.  The
        # remaining commands above have no command-execution option.
        return not any(_RG_EXECUTION_OPTION.match(token) for token in tokens[1:])
    if command == "find":
        return not any(_FIND_MUTATING_ACTION.match(token) for token in tokens[1:])
    if command != "git":
        return False

    # Permit only -C as a leading location selector, then require a known
    # observational subcommand.  Global -c/--config options can define aliases
    # or alter hooks, so they are intentionally not part of the grammar.
    index = 1
    while index < len(tokens) and tokens[index] == "-C":
        if index + 1 >= len(tokens) or not tokens[index + 1] or tokens[index + 1].startswith("-"):
            return False
        index += 2
    return (
        index < len(tokens)
        and tokens[index] in _SAFE_GIT_SUBCOMMANDS
        and not any(_GIT_EXECUTION_OPTION.match(token) for token in tokens[index + 1 :])
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
