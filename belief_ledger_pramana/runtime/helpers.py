"""Module-level helpers of the runtime. Pure functions, moved unchanged (ADR 0015)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from ..config import (
    packaged_yaml,
)
from ..engine.validity import normalize_content
from ..events import (
    EventDraft,
    canonical_json,
    content_hash,
    to_primitive,
)
from ..ingestion.adapters import SourceDescriptor
from ..ingestion.claims import (
    ClaimCandidate,
    candidate_from_structured,
)
from ..ingestion.tool import (
    redact_secrets,
    redacted_content_hash,
)
from ..models import (
    Belief,
    Integrity,
    RetractionNotice,
    SourceKind,
)

logger = logging.getLogger(__name__)


def _descendant_ids(root_id: str, dependents: Mapping[str, set[str]]) -> tuple[str, ...]:
    """Return deterministically ordered derived descendants from loaded justifications."""

    descendants: set[str] = set()
    pending = list(sorted(dependents.get(root_id, ()), reverse=True))
    while pending:
        belief_id = pending.pop()
        if belief_id in descendants:
            continue
        descendants.add(belief_id)
        pending.extend(sorted(dependents.get(belief_id, ()), reverse=True))
    return tuple(sorted(descendants))


def _ordered_belief_pair(left_id: str, right_id: str) -> tuple[str, str]:
    """Return a stable key for a symmetric belief conflict."""

    return (left_id, right_id) if left_id <= right_id else (right_id, left_id)


def _record_draft(kind: str, aggregate_type: str, aggregate_id: str, record: Any) -> EventDraft:
    return EventDraft(kind, aggregate_type, aggregate_id, {"record": to_primitive(record)})


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _correlation(kwargs: Mapping[str, Any]) -> dict[str, str]:
    fields = (
        "session_id",
        "session_key",
        "task_id",
        "turn_id",
        "tool_call_id",
        "api_request_id",
        "parent_session_id",
        "child_session_id",
    )
    return {field: value for field in fields if (value := _clean(kwargs.get(field)))}


def _args_hash(args: dict[str, Any], *, redact: bool = True) -> str:
    serialized = json.dumps(
        args, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    value, _ = redact_secrets(serialized) if redact else (serialized, False)
    return content_hash(value)


def _safe_text_hash(value: str) -> str:
    return redacted_content_hash(value)


def _contradiction_payload(left: Belief, right: Belief) -> str:
    """Canonical identity for a semantic-pair classification attempt."""

    return canonical_json(
        {
            "left": {"id": left.id, "content": left.content, "qualifiers": left.qualifiers},
            "right": {"id": right.id, "content": right.content, "qualifiers": right.qualifiers},
        }
    )


def _validate_claim_result(value: Any, *, max_claims: int = 24) -> tuple[ClaimCandidate, ...]:
    if not isinstance(value, dict) or not isinstance(value.get("claims"), list):
        raise ValueError("claim extractor result must contain a claims array")
    if len(value["claims"]) > max_claims:
        raise ValueError("claim extractor returned too many claims")
    return tuple(candidate_from_structured(item) for item in value["claims"])


def _validate_rewrite(value: Any) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("response"), str):
        raise ValueError("rewrite result must contain a response string")
    if len(value["response"]) > 16_000:
        raise ValueError("rewrite response exceeds limit")
    return str(value["response"])


def _validate_contradiction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("contradiction result must be an object")
    outcome = value.get("outcome")
    if outcome not in {"rebut", "compatible", "scope_mismatch", "uncertain"}:
        raise ValueError("contradiction outcome is invalid")
    if not isinstance(value.get("basis"), str) or not value["basis"].strip():
        raise ValueError("contradiction basis is required")
    for key in ("left_scope", "right_scope"):
        scope = value.get(key)
        if not isinstance(scope, dict) or not all(
            isinstance(item_key, str) and isinstance(item, str) for item_key, item in scope.items()
        ):
            raise ValueError(f"{key} is invalid")
    return {
        "outcome": str(outcome),
        "basis": value["basis"].strip(),
        "left_scope": value["left_scope"],
        "right_scope": value["right_scope"],
    }


def _validate_entailment(value: Any, allowed: set[tuple[int, str]]) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, dict) or not isinstance(value.get("pairs"), list):
        raise ValueError("entailment result must contain pairs")
    if len(value["pairs"]) > 30:
        raise ValueError("entailment result exceeds pair limit")
    parsed: list[dict[str, Any]] = []
    for item in value["pairs"]:
        if not isinstance(item, dict):
            raise ValueError("entailment pair must be an object")
        index = item.get("claim_index")
        belief_id = item.get("belief_id")
        entailed = item.get("entailed")
        basis = item.get("basis")
        if (
            not isinstance(index, int)
            or not isinstance(belief_id, str)
            or (index, belief_id) not in allowed
            or not isinstance(entailed, bool)
            or not isinstance(basis, str)
        ):
            raise ValueError("entailment pair is invalid or outside candidates")
        parsed.append(
            {
                "claim_index": index,
                "belief_id": belief_id,
                "entailed": entailed,
                "basis": basis[:300],
            }
        )
    return tuple(parsed)


def _action_policy_data(config: dict[str, Any]) -> dict[str, Any]:
    packaged = packaged_yaml("action-policies.yaml")
    extension_rules: list[dict[str, Any]] = []
    for raw_path in config.get("gating", {}).get("policy_files", []):
        path = Path(str(raw_path)).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError(f"action policy extension is unavailable or too large: {path}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError(f"action policy extension schema is invalid: {path}")
        rules = value.get("rules")
        if not isinstance(rules, list) or not all(isinstance(item, dict) for item in rules):
            raise ValueError(f"action policy extension rules are invalid: {path}")
        extension_rules.extend(rules)
    return {"schema_version": 1, "rules": [*extension_rules, *packaged["rules"]]}


def _source_profile_data(config: dict[str, Any]) -> dict[str, Any]:
    profiles = dict(packaged_yaml("source-profiles.yaml")["profiles"])
    for raw_path in config.get("trust", {}).get("source_profile_files", []):
        path = Path(str(raw_path)).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError(f"source profile extension is unavailable or too large: {path}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError(f"source profile extension schema is invalid: {path}")
        additions = value.get("profiles")
        if not isinstance(additions, dict):
            raise ValueError(f"source profile extension profiles are invalid: {path}")
        profiles.update(additions)
    return profiles


def _apply_source_profile(
    descriptor: SourceDescriptor,
    config: dict[str, Any],
    profiles: Mapping[str, Any] | None = None,
) -> SourceDescriptor:
    if descriptor.kind is SourceKind.RETRIEVER:
        return descriptor
    if profiles is None:
        profiles = _source_profile_data(config)
    profile_name = {
        SourceKind.TOOL: "hermes_tool",
        SourceKind.DOCUMENT: "workspace_file",
        SourceKind.WEB: "official_web" if descriptor.integrity is Integrity.SEMI else "open_web",
        SourceKind.USER: "user",
        SourceKind.MODEL: "model_component",
        SourceKind.LEDGER: "prior_ledger",
    }[descriptor.kind]
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        return descriptor
    try:
        resolved = replace(
            descriptor,
            kind=SourceKind(str(profile.get("kind", descriptor.kind.value))),
            integrity=Integrity(str(profile.get("integrity", descriptor.integrity.value))),
            competence={
                str(key): float(value)
                for key, value in dict(profile.get("competence", descriptor.competence)).items()
            },
        )
        if (
            resolved.kind is SourceKind.DOCUMENT
            and bool(config.get("ingestion", {}).get("trusted_workspace_files", False))
            and _is_relative_workspace_path(resolved.name)
        ):
            return replace(resolved, integrity=Integrity.TRUSTED)
        return resolved
    except (TypeError, ValueError) as exc:
        raise ValueError(f"source profile {profile_name} is invalid") from exc


def _is_relative_workspace_path(value: str) -> bool:
    if value.casefold().strip() in {"", "unknown", "unidentified-file"}:
        return False
    path = Path(value)
    windows_path = PureWindowsPath(value)
    return (
        not path.is_absolute()
        and not windows_path.is_absolute()
        and ".." not in path.parts
        and ".." not in windows_path.parts
        and bool(path.parts)
    )


def _explicitly_acknowledges_retraction(text: str, notice: RetractionNotice) -> bool:
    normalized = normalize_content(text)
    return notice.defeated_belief_id.casefold() in normalized and bool(
        re.search(r"\b(?:retract(?:ed|ion)?|withdrawn|superseded|incorrect)\b", normalized)
    )
