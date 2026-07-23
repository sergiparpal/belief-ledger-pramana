"""Verification task creation and passive cross-source completion."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..events import EventDraft
from ..ids import new_id
from ..ingestion.provenance import independent
from ..models import Belief, Source, VerificationMethod, VerificationTask
from ..store import LedgerStore


@dataclass(frozen=True, slots=True)
class VerificationResult:
    task: VerificationTask
    created: bool
    event_ids: tuple[str, ...]


class VerificationScheduler:
    def __init__(self, store: LedgerStore, config: dict[str, Any]) -> None:
        self.store = store
        self.config = config

    def request(
        self,
        episode_id: str,
        belief_id: str,
        method: VerificationMethod,
        *,
        k_required: int = 1,
        budget: int = 1,
    ) -> VerificationResult:
        for task in self.store.list_verification_tasks(episode_id, state="open"):
            if task.belief_id == belief_id and task.method is method:
                return VerificationResult(task, False, ())
        task = VerificationTask(
            id=new_id("verification"),
            episode_id=episode_id,
            belief_id=belief_id,
            method=method,
            k_required=max(1, min(k_required, 20)),
            budget=max(0, budget),
        )
        try:
            event = self.store.append_record(
                episode_id,
                kind="VERIFICATION_TASK_CREATED",
                aggregate_type="verification_task",
                aggregate_id=task.id,
                record=task,
            )
        except sqlite3.IntegrityError:
            for existing in self.store.list_verification_tasks(episode_id, state="open"):
                if existing.belief_id == belief_id and existing.method is method:
                    return VerificationResult(existing, False, ())
            raise
        return VerificationResult(task, True, (event.id,))

    def passive_cross_source_count(
        self,
        belief: Belief,
        beliefs: list[Belief],
        sources: dict[str, Source],
    ) -> int:
        threshold = float(self.config["ingestion"]["near_duplicate_threshold"])
        root = sources[belief.source_id].root
        matched_roots: set[str] = set()
        for candidate in beliefs:
            if (
                candidate.id == belief.id
                or candidate.normalized_content != belief.normalized_content
            ):
                continue
            candidate_root = sources[candidate.source_id].root
            if independent(
                root,
                candidate_root,
                belief.content,
                candidate.content,
                near_duplicate_threshold=threshold,
            ):
                matched_roots.add(candidate_root)
        return len(matched_roots)

    def complete(self, task: VerificationTask, result: str) -> tuple[str, ...]:
        if result not in {"confirmed", "disconfirmed", "inconclusive"}:
            raise ValueError("invalid verification result")
        events = self.store.append_events(
            task.episode_id,
            [
                EventDraft(
                    "VERIFICATION_TASK_COMPLETED",
                    "verification_task",
                    task.id,
                    {"result": result, "state": "completed"},
                )
            ],
            require_open_verification_task_id=task.id,
        )
        return tuple(event.id for event in events)
