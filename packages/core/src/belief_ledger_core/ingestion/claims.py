"""Bounded claim candidates and deterministic structured-output validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..engine.validity import normalize_content, validate_content
from ..models import Perishability, Pramana

_SENTENCE = re.compile(r"[^\n]+?(?:[.!?](?=\s|$)|$)", re.UNICODE)
_INSTRUCTION = re.compile(
    r"^\s*(?:please\s+)?(?:run|execute|delete|remove|ignore|send|publish|click|call|use|write|create|update|install|haz|ejecuta|borra)\b",
    re.IGNORECASE,
)
_ASSERTION = re.compile(
    r"\b(?:is|are|was|were|has|have|equals|requires|supports|contains|exists|confirm|authorize|approve|es|son|tiene|requiere|contiene|existe|confirmo|autorizo)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ClaimCandidate:
    content: str
    pramana: Pramana
    span_start: int
    span_end: int
    exact_excerpt: str
    qualifiers: dict[str, str] = field(default_factory=dict)
    domain: str = "general"
    perishability: Perishability = Perishability.SLOW
    speech_act: str = "asserting"
    source_identity: str = ""


@dataclass(frozen=True, slots=True)
class ClaimValidation:
    accepted: bool
    reasons: tuple[str, ...]


def deterministic_candidates(
    payload: str,
    *,
    pramana: Pramana = Pramana.SHABDA,
    max_claims: int = 24,
    require_assertion_signal: bool = True,
) -> tuple[ClaimCandidate, ...]:
    if max_claims <= 0:
        return ()
    candidates: list[ClaimCandidate] = []
    for match in _SENTENCE.finditer(payload):
        excerpt = match.group(0).strip()
        if not excerpt or excerpt.endswith("?") or _INSTRUCTION.search(excerpt):
            continue
        if require_assertion_signal and _ASSERTION.search(excerpt) is None:
            continue
        start = payload.find(excerpt, match.start(), match.end())
        end = start + len(excerpt)
        candidates.append(
            ClaimCandidate(
                content=excerpt.rstrip("."),
                pramana=pramana,
                span_start=start,
                span_end=end,
                exact_excerpt=excerpt,
            )
        )
        if len(candidates) >= max_claims:
            break
    return tuple(candidates)


def candidate_from_structured(value: dict[str, Any]) -> ClaimCandidate:
    try:
        return ClaimCandidate(
            content=str(value["content"]),
            pramana=Pramana(str(value["pramana"])),
            span_start=int(value["span_start"]),
            span_end=int(value["span_end"]),
            exact_excerpt=str(value["exact_excerpt"]),
            qualifiers={
                str(key): str(item) for key, item in dict(value.get("qualifiers", {})).items()
            },
            domain=str(value.get("domain", "general")),
            perishability=Perishability(str(value.get("perishability", "slow"))),
            speech_act=str(value.get("speech_act", "asserting")),
            source_identity=str(value.get("source_identity", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("malformed structured claim") from exc


def validate_candidate(
    candidate: ClaimCandidate,
    payload: str,
    *,
    max_words: int,
    max_chars: int = 2_000,
    allowed_source_identity: str = "",
    allowed_pramanas: set[Pramana] | None = None,
) -> ClaimValidation:
    reasons = list(validate_content(candidate.content, max_words=max_words, max_chars=max_chars))
    if not (0 <= candidate.span_start < candidate.span_end <= len(payload)):
        reasons.append("span is outside persisted evidence")
    elif payload[candidate.span_start : candidate.span_end] != candidate.exact_excerpt:
        reasons.append("exact excerpt does not match persisted evidence")
    if normalize_content(candidate.content) != normalize_content(candidate.exact_excerpt):
        reasons.append("claim content is not the normalized persisted excerpt")
    if candidate.speech_act not in {"asserting", "quoting", "speculating", "instructing"}:
        reasons.append("invalid speech act")
    if candidate.speech_act != "asserting":
        reasons.append("only asserted content can be promoted")
    if _INSTRUCTION.search(candidate.content):
        reasons.append("instruction-shaped text is not a claim")
    permitted = allowed_pramanas or {Pramana.SHABDA, Pramana.ANUPALABDHI}
    if candidate.pramana not in permitted:
        reasons.append("extractor may only propose content testimony or qualified absence")
    if (
        candidate.source_identity
        and allowed_source_identity
        and candidate.source_identity != allowed_source_identity
    ):
        reasons.append("unsupported source identity")
    return ClaimValidation(not reasons, tuple(reasons))
