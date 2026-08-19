"""User evidence typing helpers."""

from __future__ import annotations

import re

from ..models import Integrity, Source, SourceKind
from .adapters import SourceDescriptor
from .provenance import provenance_root

_SELF = re.compile(
    r"\b(?:i am|i'm|i prefer|i confirm|i authorize|i approve|my |me llamo|prefiero|confirmo|autorizo|soy)\b",
    re.IGNORECASE,
)


def user_source(sender_id: str, channel: str) -> SourceDescriptor:
    """Describe the user channel as a source.

    The competence map carries `general` only. It used to also carry `self: 0.95`, which nothing
    could reach: the deterministic extractor always emits `domain="general"`, so no belief was
    ever scored against it (recorded as F-09). An unreachable entry is worse than a missing one
    here, because of what happens the day it stops being unreachable. Whoever first gives a
    belief the `self` domain would silently grant that source 0.95 competence as a side effect of
    an unrelated change. Without the entry, `effective_competence` falls back to `general` and
    the same change yields 0.65 — the conservative value — and anyone who wants the higher number
    has to ask for it in a diff that says so.

    The self-claim privilege itself is unaffected: it lives in the trust matrix, where
    `about_self` selects `user_self` over `user_world`, and it was never a competence bump. See
    `is_user_self_claim`.
    """
    identity = sender_id.strip() or "anonymous"
    root = provenance_root(SourceKind.USER, identity=f"{channel or 'unknown'}:{identity}")
    return SourceDescriptor(
        SourceKind.USER,
        Integrity.SEMI,
        identity,
        root,
        {"general": 0.65},
    )


def is_about_user_self(content: str) -> bool:
    """Whether text is phrased as a first-person claim about the speaker.

    This looks at text alone and cannot tell where the text came from. Callers deciding whether to
    grant the self-claim privilege must use `is_user_self_claim`, which also requires the source.

    Known limitations, all deliberate and all characterised in
    `tests/unit/test_self_claim_scope.py`: there is no negation handling, so "I am not the admin"
    matches as readily as "I am the admin"; coverage is English and Spanish only, so an equivalent
    German or French claim does not match; and the pattern is satisfied by any text on the user
    channel, including text the user pasted from somewhere else.
    """
    return _SELF.search(content) is not None


def is_user_self_claim(source: Source, content: str) -> bool:
    """Whether `content` is a self-claim *and* actually came from the user's own channel.

    The self-claim privilege is not a competence bump. It selects the `user_self` trust profile
    instead of `user_world`, and those differ where it counts: at HIGH stakes `user_self` is
    svataḥ — admitted on its own authority with `k=0` — while `user_world` is parataḥ and requires
    a cross-source confirmation. Granting it to content the user did not assert would let a tool
    result, a fetched page, or a replayed prior-ledger belief admit itself uncorroborated by
    phrasing itself in the first person.

    Nothing in the ledger guarantees the caller is at a user-channel call site, so the requirement
    is enforced here rather than assumed: a non-`USER` source is refused without consulting the
    pattern at all.
    """
    if source.kind is not SourceKind.USER:
        return False
    return is_about_user_self(content)
