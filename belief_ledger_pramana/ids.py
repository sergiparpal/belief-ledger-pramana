"""Opaque, visibly typed identifiers."""

from __future__ import annotations

import re
import secrets

_PREFIXES = {
    "event": "ev_",
    "episode": "ep_",
    "evidence": "e_",
    "belief": "b_",
    "justification": "j_",
    "defeat": "d_",
    "verification": "vt_",
    "source": "src_",
    "retraction": "rn_",
    "conflict": "cf_",
    "support": "is_",
    "verdict": "cv_",
    "usage": "lu_",
    "reservation": "lr_",
}
_ID_RE = re.compile(r"^[a-z][a-z0-9]*_[A-Za-z0-9_-]{16,}$")


def new_id(kind: str) -> str:
    """Return a URL-safe, collision-resistant identifier for *kind*."""

    try:
        prefix = _PREFIXES[kind]
    except KeyError as exc:
        raise ValueError(f"unknown id kind: {kind}") from exc
    return prefix + secrets.token_urlsafe(18)


def is_typed_id(value: str, kind: str | None = None) -> bool:
    """Validate syntax and, optionally, the expected visible prefix."""

    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        return False
    return kind is None or value.startswith(_PREFIXES.get(kind, "\0"))


def require_id(value: str, kind: str) -> str:
    """Validate and return *value*, raising a stable error otherwise."""

    if not is_typed_id(value, kind):
        raise ValueError(f"invalid {kind} id")
    return value
