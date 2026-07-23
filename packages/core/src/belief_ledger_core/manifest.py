"""Versioned tool-policy manifests, schema canonicalization, and inventory."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, cast

from .events import canonical_json, content_hash

MANIFEST_SCHEMA_VERSION = 2
CANONICALIZATION_VERSION = 1
SUPPORTED_DIALECTS = {
    "https://json-schema.org/draft/2020-12/schema",
    "http://json-schema.org/draft-07/schema#",
    "",
}
ORDER_INSENSITIVE_ARRAY_FIELDS = frozenset({"required", "enum", "type"})
INFORMATIONAL_SCHEMA_FIELDS = frozenset(
    {"title", "description", "examples", "$comment", "default", "deprecated", "readOnly"}
)


class ManifestError(ValueError):
    pass


class InventoryStatus(StrEnum):
    COVERED = "covered"
    READ_ONLY = "explicit_read_only"
    EFFECTFUL = "explicit_effectful"
    DRIFTED = "drifted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    id: str
    revision: str
    effectful: bool
    base_stakes: str
    target_fields: tuple[str, ...]
    preconditions: tuple[str, ...]
    approval_policy: str
    minimum_source_integrity: str
    canonicalization_version: int
    exact: tuple[str, ...] = ()
    pattern: str | None = None
    namespace: str | None = None
    input_schema_digest: str | None = None
    priority: int = 0
    active: bool = True


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    schema_version: int
    name: str
    namespace: str
    description: str
    input_schema: dict[str, Any]
    schema_digest: str

    @classmethod
    def create(
        cls,
        name: str,
        input_schema: Mapping[str, Any],
        *,
        namespace: str = "",
        description: str = "",
    ) -> ToolDescriptor:
        schema = copy.deepcopy(dict(input_schema))
        return cls(
            1,
            name.strip(),
            namespace.strip(),
            description.strip(),
            schema,
            schema_digest(schema),
        )


@dataclass(frozen=True, slots=True)
class InventoryItem:
    schema_version: int
    descriptor: ToolDescriptor
    status: InventoryStatus
    policy_id: str | None
    reason_code: str


class ToolPolicyManifest:
    def __init__(self, rules: tuple[ToolPolicy, ...], *, source_schema_version: int) -> None:
        self.schema_version = MANIFEST_SCHEMA_VERSION
        self.source_schema_version = source_schema_version
        self.rules = rules
        self._validate()

    @classmethod
    def load(cls, value: Mapping[str, Any], *, mode: str = "enforce") -> ToolPolicyManifest:
        data = dict(value)
        version = data.get("schema_version")
        if type(version) is int and version == 1:
            normalized = _normalize_v1(data)
            return cls(
                tuple(_parse_rule(item, mode=mode) for item in normalized), source_schema_version=1
            )
        if (
            type(version) is not int
            or version != MANIFEST_SCHEMA_VERSION
            or not isinstance(data.get("rules"), list)
        ):
            raise ManifestError("tool manifest must use schema_version 1 or 2 with a rules list")
        unknown = set(data) - {"schema_version", "canonicalization_version", "rules"}
        if unknown and mode != "observe":
            raise ManifestError(f"unknown manifest fields: {', '.join(sorted(unknown))}")
        canonicalization = data.get("canonicalization_version", CANONICALIZATION_VERSION)
        if canonicalization != CANONICALIZATION_VERSION:
            raise ManifestError("unsupported manifest canonicalization_version")
        return cls(
            tuple(_parse_rule(item, mode=mode) for item in data["rules"]),
            source_schema_version=2,
        )

    def match(self, name: str, namespace: str = "") -> ToolPolicy | None:
        normalized = name.casefold().strip()
        exact = [
            rule
            for rule in self.rules
            if rule.active
            and (rule.namespace is None or rule.namespace == namespace)
            and normalized in rule.exact
        ]
        if exact:
            return max(exact, key=lambda rule: (rule.namespace == namespace, rule.priority))
        patterns = [
            rule
            for rule in self.rules
            if rule.active
            and rule.pattern
            and (rule.namespace is None or rule.namespace == namespace)
            and re.fullmatch(rule.pattern, normalized)
        ]
        if not patterns:
            return None
        specificity = max((rule.namespace == namespace, rule.priority) for rule in patterns)
        winners = [
            rule for rule in patterns if (rule.namespace == namespace, rule.priority) == specificity
        ]
        if len(winners) != 1:
            raise ManifestError(f"ambiguous policy patterns for {namespace}:{name}")
        return winners[0]

    def classify_inventory(
        self, descriptors: tuple[ToolDescriptor, ...], *, complete: bool
    ) -> tuple[InventoryItem, ...]:
        items: list[InventoryItem] = []
        seen: set[tuple[str, str]] = set()
        for descriptor in sorted(descriptors, key=lambda item: (item.namespace, item.name)):
            key = (descriptor.namespace, descriptor.name)
            if key in seen:
                raise ManifestError(
                    f"duplicate tool inventory entry: {descriptor.namespace}:{descriptor.name}"
                )
            seen.add(key)
            policy = self.match(descriptor.name, descriptor.namespace)
            if policy is None:
                item = InventoryItem(1, descriptor, InventoryStatus.UNKNOWN, None, "NO_POLICY")
            elif (
                policy.input_schema_digest
                and policy.input_schema_digest != descriptor.schema_digest
            ):
                item = InventoryItem(
                    1,
                    descriptor,
                    InventoryStatus.DRIFTED,
                    policy.id,
                    "CANONICAL_SCHEMA_DRIFT",
                )
            else:
                status = (
                    InventoryStatus.EFFECTFUL if policy.effectful else InventoryStatus.READ_ONLY
                )
                item = InventoryItem(1, descriptor, status, policy.id, "POLICY_MATCHED")
            items.append(item)
        if not complete:
            # The returned tools remain useful, but callers must expose this reason
            # and cannot interpret absence as proof that no other tools exist.
            items.append(
                InventoryItem(
                    1,
                    ToolDescriptor.create("__inventory_incomplete__", {}),
                    InventoryStatus.UNKNOWN,
                    None,
                    "INVENTORY_INCOMPLETE",
                )
            )
        return tuple(items)

    def scaffold(self, descriptor: ToolDescriptor) -> dict[str, Any]:
        return {
            "id": f"review-{_slug(descriptor.namespace, descriptor.name)}",
            "revision": "REVIEW_REQUIRED",
            "active": False,
            "exact": [descriptor.name.casefold()],
            "namespace": descriptor.namespace or None,
            "effectful": True,
            "base_stakes": "high",
            "target_fields": [],
            "preconditions": ["operator_review_required"],
            "approval_policy": "required",
            "minimum_source_integrity": "trusted",
            "canonicalization_version": CANONICALIZATION_VERSION,
            "input_schema_digest": descriptor.schema_digest,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "rules": [asdict(rule) for rule in self.rules],
        }

    def _validate(self) -> None:
        identifiers: set[str] = set()
        exact: dict[tuple[str | None, str], ToolPolicy] = {}
        patterns: dict[tuple[str | None, str], ToolPolicy] = {}
        for rule in self.rules:
            if rule.id in identifiers:
                raise ManifestError(f"duplicate policy id: {rule.id}")
            identifiers.add(rule.id)
            for name in rule.exact:
                key = (rule.namespace, name)
                if key in exact:
                    raise ManifestError(f"duplicate exact policy: {rule.namespace}:{name}")
                exact[key] = rule
            if rule.pattern:
                key = (rule.namespace, rule.pattern)
                previous = patterns.get(key)
                if previous and previous.priority == rule.priority:
                    raise ManifestError(f"ambiguous duplicate policy pattern: {rule.pattern}")
                patterns[key] = rule


def canonicalize_schema(schema: Mapping[str, Any], *, version: int = 1) -> dict[str, Any]:
    if version != CANONICALIZATION_VERSION:
        raise ManifestError("unsupported schema canonicalization version")
    root = copy.deepcopy(dict(schema))
    dialect = str(root.get("$schema", ""))
    if dialect not in SUPPORTED_DIALECTS:
        raise ManifestError(f"unsupported JSON Schema dialect: {dialect}")

    def visit(
        value: Any,
        stack: tuple[str, ...] = (),
        field_name: str | None = None,
    ) -> Any:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    raise ManifestError("only local JSON Pointer $ref values are supported")
                if reference in stack:
                    raise ManifestError("cyclic local $ref is not supported")
                target: Any = root
                for token in reference[2:].split("/"):
                    token = token.replace("~1", "/").replace("~0", "~")
                    if not isinstance(target, dict) or token not in target:
                        raise ManifestError(f"unresolved local $ref: {reference}")
                    target = target[token]
                siblings = {key: item for key, item in value.items() if key != "$ref"}
                if siblings:
                    if "draft-07" in dialect or not dialect:
                        return visit(target, (*stack, reference), field_name)
                    return visit(
                        {"allOf": [target, siblings]},
                        (*stack, reference),
                        field_name,
                    )
                return visit(target, (*stack, reference), field_name)
            return {
                str(key): visit(item, stack, str(key))
                for key, item in sorted(value.items())
                if key not in INFORMATIONAL_SCHEMA_FIELDS and key not in {"$defs", "definitions"}
            }
        if isinstance(value, list):
            items = [visit(item, stack) for item in value]
            if field_name in ORDER_INSENSITIVE_ARRAY_FIELDS:
                return sorted(items, key=canonical_json)
            return items
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    return cast(dict[str, Any], visit(root))


def schema_digest(schema: Mapping[str, Any], *, version: int = 1) -> str:
    canonical = {
        "canonicalization_version": version,
        "schema": canonicalize_schema(schema, version=version),
    }
    return content_hash(canonical_json(canonical))


def _normalize_v1(data: dict[str, Any]) -> list[dict[str, Any]]:
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise ManifestError("v1 action policy registry requires rules")
    normalized: list[dict[str, Any]] = []
    for item in rules:
        if not isinstance(item, dict):
            raise ManifestError("v1 policy rule must be a mapping")
        value = dict(item)
        allow_approval = value.get("allow_human_approval")
        if not isinstance(allow_approval, bool):
            raise ManifestError("v1 allow_human_approval must be a boolean")
        value.update(
            {
                "revision": content_hash(canonical_json(item)),
                "approval_policy": "allowed" if allow_approval else "none",
                "minimum_source_integrity": item.get("minimum_priority", "trusted"),
                "canonicalization_version": CANONICALIZATION_VERSION,
                "active": True,
            }
        )
        normalized.append(value)
    return normalized


def _parse_rule(value: Any, *, mode: str) -> ToolPolicy:
    if not isinstance(value, dict):
        raise ManifestError("tool policy rule must be a mapping")
    allowed = {
        "id",
        "revision",
        "effectful",
        "base_stakes",
        "target_fields",
        "preconditions",
        "approval_policy",
        "minimum_source_integrity",
        "canonicalization_version",
        "exact",
        "pattern",
        "namespace",
        "input_schema_digest",
        "priority",
        "active",
        "allow_human_approval",
        "minimum_priority",
    }
    unknown = set(value) - allowed
    if unknown and mode != "observe":
        raise ManifestError(f"unknown policy fields: {', '.join(sorted(unknown))}")
    required = {"id", "revision", "effectful", "base_stakes"}
    missing = sorted(required - set(value))
    if missing:
        raise ManifestError(f"policy rule missing fields: {', '.join(missing)}")
    policy_id = _required_text(value["id"], "policy id")
    revision = _required_text(value["revision"], f"policy {policy_id} revision")
    if not isinstance(value["effectful"], bool):
        raise ManifestError(f"policy {policy_id} effectful must be a boolean")
    base_stakes = _required_text(value["base_stakes"], f"policy {policy_id} base_stakes")
    if base_stakes not in {"low", "med", "high", "critical"}:
        raise ManifestError(f"policy {policy_id} base_stakes is invalid")
    exact_values = _string_tuple(value.get("exact", ()), f"policy {policy_id} exact")
    target_fields = _string_tuple(
        value.get("target_fields", ()), f"policy {policy_id} target_fields"
    )
    preconditions = _string_tuple(
        value.get("preconditions", ()), f"policy {policy_id} preconditions"
    )
    pattern = value.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str) or not pattern:
            raise ManifestError(f"policy {policy_id} pattern must be a non-empty string")
        if not pattern.startswith("^") or not pattern.endswith("$"):
            raise ManifestError(f"policy {policy_id} pattern must be anchored")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ManifestError(f"policy {policy_id} pattern is invalid") from exc
    if not exact_values and pattern is None:
        raise ManifestError(f"policy {policy_id} must define exact names or a pattern")
    approval_raw = value.get("approval_policy", "none")
    if not isinstance(approval_raw, str):
        raise ManifestError("approval_policy must be a string")
    approval = approval_raw
    if approval not in {"none", "allowed", "required"}:
        raise ManifestError("approval_policy must be none, allowed, or required")
    integrity_raw = value.get("minimum_source_integrity", "trusted")
    if not isinstance(integrity_raw, str):
        raise ManifestError("minimum_source_integrity must be a string")
    integrity = integrity_raw
    if integrity not in {"trusted", "semi", "untrusted"}:
        raise ManifestError("minimum_source_integrity is invalid")
    canonicalization_raw = value.get("canonicalization_version", CANONICALIZATION_VERSION)
    if type(canonicalization_raw) is not int:
        raise ManifestError("policy canonicalization_version must be an integer")
    canonicalization = canonicalization_raw
    if canonicalization != CANONICALIZATION_VERSION:
        raise ManifestError("unsupported policy canonicalization_version")
    namespace_raw = value.get("namespace")
    if namespace_raw is not None and not isinstance(namespace_raw, str):
        raise ManifestError(f"policy {policy_id} namespace must be a string or null")
    priority_raw = value.get("priority", 0)
    if type(priority_raw) is not int:
        raise ManifestError(f"policy {policy_id} priority must be an integer")
    active_raw = value.get("active", True)
    if not isinstance(active_raw, bool):
        raise ManifestError(f"policy {policy_id} active must be a boolean")
    schema_digest_raw = value.get("input_schema_digest")
    if schema_digest_raw is not None and (
        not isinstance(schema_digest_raw, str)
        or re.fullmatch(r"[0-9a-f]{64}", schema_digest_raw) is None
    ):
        raise ManifestError(f"policy {policy_id} input_schema_digest must be a SHA-256 digest")
    return ToolPolicy(
        id=policy_id,
        revision=revision,
        effectful=value["effectful"],
        base_stakes=base_stakes,
        target_fields=target_fields,
        preconditions=preconditions,
        approval_policy=approval,
        minimum_source_integrity=integrity,
        canonicalization_version=canonicalization,
        exact=tuple(item.casefold() for item in exact_values),
        pattern=pattern,
        namespace=namespace_raw.strip() if namespace_raw and namespace_raw.strip() else None,
        input_schema_digest=schema_digest_raw,
        priority=priority_raw,
        active=active_raw,
    )


def _required_text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 256
        or "\n" in value
        or "\r" in value
    ):
        raise ManifestError(f"{label} must be a non-empty single-line string")
    return value.strip()


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() and "\n" not in item and "\r" not in item
        for item in value
    ):
        raise ManifestError(f"{label} must be a list of non-empty strings")
    normalized = tuple(item.strip() for item in value)
    if len(set(normalized)) != len(normalized):
        raise ManifestError(f"{label} contains duplicates")
    return normalized


def _slug(namespace: str, name: str) -> str:
    material = f"{namespace}-{name}" if namespace else name
    return re.sub(r"[^a-z0-9]+", "-", material.casefold()).strip("-") or "tool"
