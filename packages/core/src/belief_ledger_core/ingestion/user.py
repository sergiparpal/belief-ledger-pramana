"""User evidence typing helpers."""

from __future__ import annotations

import re

from ..models import Integrity, SourceKind
from .adapters import SourceDescriptor
from .provenance import provenance_root

_SELF = re.compile(
    r"\b(?:i am|i'm|i prefer|i confirm|i authorize|i approve|my |me llamo|prefiero|confirmo|autorizo|soy)\b",
    re.IGNORECASE,
)


def user_source(sender_id: str, channel: str) -> SourceDescriptor:
    identity = sender_id.strip() or "anonymous"
    root = provenance_root(SourceKind.USER, identity=f"{channel or 'unknown'}:{identity}")
    return SourceDescriptor(
        SourceKind.USER,
        Integrity.SEMI,
        identity,
        root,
        {"self": 0.95, "general": 0.65},
    )


def is_about_user_self(content: str) -> bool:
    return _SELF.search(content) is not None
