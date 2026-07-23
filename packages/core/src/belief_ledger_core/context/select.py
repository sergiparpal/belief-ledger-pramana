"""Deterministic lexical/graph-aware belief selection."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..engine.priority import priority_trace
from ..engine.validity import normalize_content
from ..models import Belief, Conflict, RetractionNotice, Source, Status

_TOKEN = re.compile(r"[\w.-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Selection:
    beliefs: tuple[Belief, ...]
    conflicts: tuple[Conflict, ...]
    retractions: tuple[RetractionNotice, ...]
    relevance: dict[str, float]


def select_beliefs(
    beliefs: Iterable[Belief],
    sources: Mapping[str, Source],
    *,
    query: str,
    conflicts: Iterable[Conflict] = (),
    retractions: Iterable[RetractionNotice] = (),
    precondition_ids: Iterable[str] = (),
    retrieval_ids: Iterable[str] = (),
    config: dict[str, Any],
) -> Selection:
    belief_map = {belief.id: belief for belief in beliefs}
    query_tokens = _tokens(query)
    content_tokens = {belief.id: _tokens(belief.content) for belief in belief_map.values()}
    scores = {
        belief.id: lexical_score(query_tokens, content_tokens[belief.id])
        for belief in belief_map.values()
    }
    for rank, belief_id in enumerate(retrieval_ids):
        if belief_id in scores:
            scores[belief_id] += max(0.0, 1.0 - rank / 1_000)
    conflict_list = tuple(sorted(conflicts, key=lambda item: item.id))
    retraction_list = tuple(sorted(retractions, key=lambda item: (item.created_turn, item.id)))
    mandatory = set(precondition_ids)
    for conflict in conflict_list:
        mandatory.update((conflict.left_belief_id, conflict.right_belief_id))

    candidates: list[Belief] = []
    for belief in belief_map.values():
        if belief.status in {Status.OUT, Status.QUARANTINED}:
            continue
        if belief.domain == "monitoring" and belief.id not in mandatory and scores[belief.id] <= 0:
            continue
        if (
            belief.status is Status.PENDING
            and config["context"].get("pending_only_when_relevant", True)
            and belief.id not in mandatory
            and scores[belief.id] <= 0
        ):
            continue
        candidates.append(belief)

    priority_values = {
        belief.id: priority_trace(belief, sources[belief.source_id], config).value
        for belief in candidates
    }

    def key(belief: Belief) -> tuple[Any, ...]:
        return (
            1 if belief.id in mandatory else 0,
            scores[belief.id],
            *priority_values[belief.id],
            belief.id,
        )

    candidates.sort(key=key, reverse=True)
    limit = int(config["context"]["max_beliefs"])
    chosen: list[Belief] = []
    chosen_ids: set[str] = set()
    for belief in candidates:
        if len(chosen) >= limit:
            break
        _add_with_premises(
            belief,
            belief_map,
            chosen,
            chosen_ids,
            max_depth=int(config["context"]["max_graph_depth"]),
            limit=limit,
        )
    return Selection(tuple(chosen), conflict_list, retraction_list, scores)


def lexical_score(query_tokens: set[str], content_tokens: set[str]) -> float:
    if not query_tokens or not content_tokens:
        return 0.0
    shared = len(query_tokens & content_tokens)
    return shared / max(1, len(query_tokens | content_tokens))


def _add_with_premises(
    belief: Belief,
    belief_map: Mapping[str, Belief],
    chosen: list[Belief],
    chosen_ids: set[str],
    *,
    max_depth: int,
    limit: int,
    depth: int = 0,
    visiting: set[str] | None = None,
) -> None:
    if belief.id in chosen_ids or len(chosen) >= limit:
        return
    current_path = visiting if visiting is not None else set()
    if belief.id in current_path:
        return
    current_path.add(belief.id)
    if depth >= max_depth:
        premise_ids: list[str] = []
    else:
        premise_ids = sorted(
            {
                premise
                for justification in belief.justifications
                for premise in justification.premises
            }
        )
    for premise_id in premise_ids:
        premise = belief_map.get(premise_id)
        if premise and premise.status in {Status.IN, Status.PENDING}:
            _add_with_premises(
                premise,
                belief_map,
                chosen,
                chosen_ids,
                max_depth=max_depth,
                limit=limit,
                depth=depth + 1,
                visiting=current_path,
            )
    current_path.remove(belief.id)
    if belief.id not in chosen_ids and len(chosen) < limit:
        chosen.append(belief)
        chosen_ids.add(belief.id)


def _tokens(text: str) -> set[str]:
    return {item for item in _TOKEN.findall(normalize_content(text)) if len(item) > 1}
