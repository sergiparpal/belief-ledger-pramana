"""Deterministic extraction of declarative factual response claims."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]+`")
_SENTENCE = re.compile(r"[^\n]+?(?:[.!?](?=\s|$)|$)", re.UNICODE)
_FACTUAL = re.compile(
    r"\b(?:is|are|was|were|has|have|will|does|did|requires|supports|contains|exists|equals|implemented|created|updated|fixed|passes|uses|runs|depends|depends on|installed|available|failed|succeeded|es|son|tiene|requiere|contiene|existe)\b",
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


def extract_claims(
    response: str, *, pending_marker: str, require_coverage: bool = False
) -> tuple[ExtractedClaim, ...]:
    # High-stakes coverage includes fenced output: factual comments, command
    # output, and serialized data are still assertions delivered to the user.
    masked = response if require_coverage else _mask(_CODE_FENCE, response)
    masked = _mask(_INLINE_CODE, masked)
    claims: list[ExtractedClaim] = []
    for match in _SENTENCE.finditer(masked):
        text = response[match.start() : match.end()].strip()
        if not text or text.endswith("?") or text.startswith("#") or _SPECULATION.search(text):
            continue
        if _FACTUAL.search(text) is None and not (
            require_coverage and _looks_substantive_declarative(text)
        ):
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


def _looks_substantive_declarative(text: str) -> bool:
    """Fail closed on prose that evades the narrow factual-verb heuristic."""

    clean = _CITATION.sub("", text)
    clean = re.sub(r"^\s*[-*]\s*", "", clean).strip()
    words = re.findall(r"[\w.-]+", clean, flags=re.UNICODE)
    if len(words) < 3 and not any(char.isdigit() for char in clean):
        return False
    return clean.casefold() not in {"thanks", "thank you", "ok", "okay", "done"}


def strip_citations(text: str, pending_marker: str) -> str:
    result = _CITATION.sub("", text)
    result = re.sub(re.escape(pending_marker), "", result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip(" .")


def _mask(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda match: " " * (match.end() - match.start()), text)
