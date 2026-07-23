"""Privacy-preserving evidence preparation for Hermes tool results."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..events import content_hash

_SENSITIVE_FIELD_PATTERN = (
    r"(?:"
    r"[a-z0-9_.-]*?(?:api[_-]?key|access[_-]?key(?:[_-]?id)?|account[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?token|token|auth(?:orization)?|"
    r"secret(?:[_-]?key)?|password|passwd|credential|private[_-]?key|"
    r"client[_-]?secret|session(?:[_-]?id)?|cookie)"
    r")"
)
_SENSITIVE_FIELD = re.compile(_SENSITIVE_FIELD_PATTERN, re.IGNORECASE)
_QUOTED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>[\"']?{_SENSITIVE_FIELD_PATTERN}[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_BARE_ASSIGNMENT = re.compile(
    rf"(?P<prefix>[\"']?{_SENSITIVE_FIELD_PATTERN}[\"']?\s*[:=]\s*)"
    r"(?P<value>[^\s,;}\]]+)",
    re.IGNORECASE,
)
_AUTHORIZATION = re.compile(r"(?im)^(?P<prefix>\s*(?:proxy-)?authorization\s*:\s*).+$")
_COOKIE = re.compile(r"(?im)^(?P<prefix>\s*(?:set-cookie|cookie)\s*:\s*).+$")
_URI_CREDENTIALS = re.compile(
    r"\b(?P<prefix>[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@",
    re.IGNORECASE,
)
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----.*?(?:-----END(?: [A-Z0-9]+)* PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA|A3T[A-Z]|AGPA|AROA)[A-Z0-9]{16}\b")
_KNOWN_TOKENS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
)


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    payload: str | None
    full_hash: str
    redacted: bool
    excerpt_start: int | None
    excerpt_end: int | None
    observed_chars: int


def prepare_evidence(
    result: str, *, mode: str, max_excerpt_chars: int, redact: bool = True
) -> PreparedEvidence:
    """Redact before deriving any persistent representation of an observation."""

    redacted_text, redacted = redact_secrets(result) if redact else (result, False)
    # When privacy redaction is enabled, do not persist a digest that can
    # confirm a detected short secret.  When the operator explicitly disables
    # redaction, the digest must still commit to the payload that is persisted.
    full_hash = redacted_content_hash(result) if redact else content_hash(result)
    if mode == "hash_only":
        return PreparedEvidence(None, full_hash, redacted, None, None, len(result))
    if mode == "full":
        return PreparedEvidence(
            redacted_text, full_hash, redacted, 0, len(redacted_text), len(result)
        )
    if mode != "excerpt":
        raise ValueError(f"unsupported evidence mode: {mode}")
    limit = max(0, max_excerpt_chars)
    excerpt = redacted_text[:limit]
    return PreparedEvidence(excerpt, full_hash, redacted, 0, len(excerpt), len(result))


def redact_secrets(text: str) -> tuple[str, bool]:
    """Remove common credentials from structured and unstructured observations.

    This is a privacy control, not a claim that arbitrary text can be perfectly
    classified. It deliberately prefers false positives to persisting a
    credential in the evidence ledger.
    """

    result, _ = _redact_json_document(text)
    result = _AUTHORIZATION.sub(lambda match: f"{match.group('prefix')}[REDACTED]", result)
    result = _COOKIE.sub(lambda match: f"{match.group('prefix')}[REDACTED]", result)
    result = _URI_CREDENTIALS.sub(lambda match: f"{match.group('prefix')}[REDACTED]@", result)
    result = _QUOTED_ASSIGNMENT.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}"
        ),
        result,
    )
    result = _BARE_ASSIGNMENT.sub(lambda match: f"{match.group('prefix')}[REDACTED]", result)
    result = _PEM_PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", result)
    result = _JWT.sub("[REDACTED JWT]", result)
    result = _AWS_ACCESS_KEY.sub("[REDACTED AWS ACCESS KEY]", result)
    for pattern in _KNOWN_TOKENS:
        result = pattern.sub("[REDACTED]", result)
    return result, result != text


def redacted_content_hash(value: str | bytes) -> str:
    """Return a stable digest that cannot be used to confirm detected secrets."""

    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return content_hash(redact_secrets(text)[0])


def _redact_json_document(text: str) -> tuple[str, bool]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return text, False
    redacted, changed = _redact_json_value(value)
    if not changed:
        return text, False
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":")), True


def _redact_json_value(value: Any, *, sensitive: bool = False) -> tuple[Any, bool]:
    if sensitive:
        return "[REDACTED]", True
    if isinstance(value, dict):
        object_result: dict[str, Any] = {}
        changed = False
        for key, item in value.items():
            item_redacted, item_changed = _redact_json_value(
                item,
                sensitive=bool(_SENSITIVE_FIELD.fullmatch(str(key))),
            )
            object_result[str(key)] = item_redacted
            changed = changed or item_changed
        return object_result, changed
    if isinstance(value, list):
        list_result: list[Any] = []
        changed = False
        for item in value:
            item_redacted, item_changed = _redact_json_value(item)
            list_result.append(item_redacted)
            changed = changed or item_changed
        return list_result, changed
    return value, False
