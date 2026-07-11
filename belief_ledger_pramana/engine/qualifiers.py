"""Deterministic qualifier normalization before contradiction checks (R7)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

SUPPORTED_QUALIFIERS = {
    "as_of",
    "valid_from",
    "valid_to",
    "scope",
    "jurisdiction",
    "perspective",
    "units",
    "version",
    "assumptions",
}
_ALIASES = {
    "assumes": "assumptions",
    "unit": "units",
    "validity_start": "valid_from",
    "validity_end": "valid_to",
}


@dataclass(frozen=True, slots=True)
class ScopeReconciliation:
    compatible: bool
    left: dict[str, str]
    right: dict[str, str]
    normalized_scope: dict[str, str]
    reason: str


def canonicalize_qualifiers(qualifiers: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in (qualifiers or {}).items():
        key = _ALIASES.get(str(raw_key).strip().lower(), str(raw_key).strip().lower())
        if key not in SUPPORTED_QUALIFIERS:
            continue
        value = re.sub(r"\s+", " ", str(raw_value).strip())
        if not value:
            continue
        if key in {"as_of", "valid_from", "valid_to"}:
            value = _canonical_date(value)
        elif key in {"scope", "jurisdiction", "perspective", "units", "version"}:
            value = value.casefold()
        result[key] = value
    return dict(sorted(result.items()))


def reconcile_qualifiers(
    left: Mapping[str, str] | None, right: Mapping[str, str] | None
) -> ScopeReconciliation:
    lq = canonicalize_qualifiers(left)
    rq = canonicalize_qualifiers(right)
    for key in ("scope", "jurisdiction", "perspective", "version", "assumptions"):
        if key in lq and key in rq and lq[key] != rq[key]:
            return ScopeReconciliation(False, lq, rq, {}, f"disjoint {key}")

    left_interval = _interval(lq)
    right_interval = _interval(rq)
    if left_interval and right_interval and not _overlaps(left_interval, right_interval):
        return ScopeReconciliation(False, lq, rq, {}, "disjoint validity intervals")
    if "as_of" in lq and "as_of" in rq and lq["as_of"] != rq["as_of"]:
        return ScopeReconciliation(False, lq, rq, {}, "different as_of qualifiers")

    shared = {key: value for key, value in lq.items() if rq.get(key) == value}
    if "units" in lq and "units" in rq and not units_compatible(lq["units"], rq["units"]):
        return ScopeReconciliation(False, lq, rq, shared, "incompatible units")
    return ScopeReconciliation(True, lq, rq, shared, "qualifiers overlap")


def units_compatible(left: str, right: str) -> bool:
    aliases = {
        "byte": "bytes",
        "b": "bytes",
        "kilobyte": "kb",
        "kibibyte": "kib",
        "second": "seconds",
        "sec": "seconds",
        "s": "seconds",
    }
    return aliases.get(left.casefold(), left.casefold()) == aliases.get(
        right.casefold(), right.casefold()
    )


def _canonical_date(value: str) -> str:
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC)
        return parsed.isoformat().replace("+00:00", "Z")
    except ValueError:
        try:
            return date.fromisoformat(cleaned).isoformat()
        except ValueError:
            return value


def _interval(qualifiers: Mapping[str, str]) -> tuple[str, str] | None:
    start = qualifiers.get("valid_from") or qualifiers.get("as_of")
    end = qualifiers.get("valid_to") or qualifiers.get("as_of")
    return (start or "0000", end or "9999") if start or end else None


def _overlaps(left: tuple[str, str], right: tuple[str, str]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]
