"""Deterministic extraction of declarative factual response claims."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]+`")
_SENTENCE = re.compile(r"[^\n]+?(?:[.!?](?=\s|$)|$)", re.UNICODE)
_FACTUAL = re.compile(
    r"\b(?:is|are|was|were|has|have|will|does|did|requires|supports|contains|exists|equals|implemented|created|updated|fixed|passes|es|son|tiene|requiere|contiene|existe)\b",
    re.IGNORECASE,
)
_SPECULATION = re.compile(
    r"^\s*(?:[-*]\s*)?(?:speculation|possibly|perhaps|maybe|hypothesis|unverified|especulación|quizá)\s*:",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\[(b_[A-Za-z0-9_-]+)\]")


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    text: str
    start: int
    end: int
    cited_beliefs: tuple[str, ...]
    pending_marked: bool


def extract_claims(response: str, *, pending_marker: str) -> tuple[ExtractedClaim, ...]:
    masked = _mask(_CODE_FENCE, response)
    masked = _mask(_INLINE_CODE, masked)
    claims: list[ExtractedClaim] = []
    for match in _SENTENCE.finditer(masked):
        text = response[match.start() : match.end()].strip()
        if not text or text.endswith("?") or text.startswith("#") or _SPECULATION.search(text):
            continue
        if _FACTUAL.search(text) is None:
            continue
        claims.append(
            ExtractedClaim(
                text=text,
                start=match.start(),
                end=match.end(),
                cited_beliefs=tuple(dict.fromkeys(_CITATION.findall(text))),
                pending_marked=pending_marker.casefold() in text.casefold(),
            )
        )
    return tuple(claims)


def strip_citations(text: str, pending_marker: str) -> str:
    result = _CITATION.sub("", text)
    result = re.sub(re.escape(pending_marker), "", result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip(" .")


def _mask(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda match: " " * (match.end() - match.start()), text)
