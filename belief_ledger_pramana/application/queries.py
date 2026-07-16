"""Read-only ledger query and explanation use cases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..engine.priority import priority_trace
from ..events import to_primitive
from ..models import Pramana, Status
from ..ports import LedgerQueryReader


class LedgerQueryService:
    """Serve query/explain views through the smallest read-model port."""

    def __init__(self, store: LedgerQueryReader, config: Mapping[str, Any]) -> None:
        self._store = store
        self._config = config

    def query(
        self,
        episode_id: str,
        text: str,
        *,
        statuses: Sequence[Status] = (),
        pramanas: Sequence[Pramana] = (),
        limit: int = 20,
        expand_graph: bool = False,
    ) -> list[dict[str, Any]]:
        from ..engine.validity import normalize_content

        wanted = set(normalize_content(text).split())
        beliefs = self._store.list_beliefs(
            episode_id,
            statuses=statuses or None,
            pramanas=pramanas or None,
            limit=5_000,
        )
        scored = []
        for belief in beliefs:
            score = len(wanted & set(belief.normalized_content.split()))
            if not wanted or score:
                scored.append((score, belief))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            {
                "id": belief.id,
                "content": belief.content,
                "pramana": belief.pramana.value,
                "status": belief.status.value,
                "source_id": belief.source_id,
                "qualifiers": belief.qualifiers,
                "premises": [
                    premise
                    for justification in belief.justifications
                    for premise in justification.premises
                ]
                if expand_graph
                else [],
            }
            for _, belief in scored[: max(1, min(limit, 100))]
        ]

    def explain(self, episode_id: str, belief_id: str, *, depth: int = 4) -> dict[str, Any]:
        belief = self._store.get_belief(belief_id)
        if belief is None or belief.episode_id != episode_id:
            raise ValueError("belief does not exist in this episode")
        source = self._store.get_source(belief.source_id)
        if source is None:
            raise RuntimeError("belief source projection is missing")
        all_sources = {item.id: item for item in self._store.list_sources(episode_id)}
        trace = priority_trace(belief, source, dict(self._config))
        defeats = [
            edge
            for edge in self._store.list_defeats(episode_id)
            if edge.attacker == belief_id or edge.target == belief_id
        ]
        events = [
            event
            for event in self._store.events(episode_id)
            if event.aggregate_id == belief_id or event.payload.get("belief_id") == belief_id
        ]
        tasks = [
            task
            for task in self._store.list_verification_tasks(episode_id, state=None)
            if task.belief_id == belief_id
        ]
        premise_by_id = self._store.get_beliefs(
            premise_id
            for justification in belief.justifications
            for premise_id in justification.premises
        )
        return {
            "belief": to_primitive(belief),
            "source": to_primitive(source),
            "priority": to_primitive(trace),
            "defeats": [to_primitive(edge) for edge in defeats],
            "transitions": [
                to_primitive(event) for event in events[-max(1, min(depth * 5, 100)) :]
            ],
            "verification": [to_primitive(task) for task in tasks],
            "live_justifications": [
                justification.id
                for justification in belief.justifications
                if all(
                    (premise := premise_by_id.get(premise_id)) is not None
                    and premise.status is Status.IN
                    for premise_id in justification.premises
                )
            ],
            "source_count": len(all_sources),
        }
