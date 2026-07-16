"""Verification scheduling application service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ..config import ConfigSnapshot
from ..events import EventDraft
from ..ids import new_id
from ..ingestion.provenance import independent
from ..models import Belief, Source, VerificationMethod, VerificationTask
from ..ports import EventWriter, VerificationTaskReader


@dataclass(frozen=True, slots=True)
class VerificationResult:
    task: VerificationTask
    created: bool
    event_ids: tuple[str, ...]


class VerificationScheduler:
    """Create and complete bounded verification tasks through a ledger port."""

    def __init__(
        self,
        reader: VerificationTaskReader,
        config: ConfigSnapshot,
        *,
        writer: EventWriter | None = None,
    ) -> None:
        self._reader = reader
        # Legacy callers pass one LedgerStore that structurally satisfies both
        # ports. New composition roots provide a writer explicitly.
        self._writer = writer if writer is not None else cast(EventWriter, reader)
        self.config = config
        self.settings = config.settings

    def request(
        self,
        episode_id: str,
        belief_id: str,
        method: VerificationMethod,
        *,
        k_required: int = 1,
        budget: int = 1,
    ) -> VerificationResult:
        existing = self._open_task(episode_id, belief_id, method)
        if existing is not None:
            return VerificationResult(existing, False, ())
        task = VerificationTask(
            id=new_id("verification"),
            episode_id=episode_id,
            belief_id=belief_id,
            method=method,
            k_required=max(1, min(k_required, 20)),
            budget=max(0, budget),
        )
        try:
            event = self._writer.append_record(
                episode_id,
                kind="VERIFICATION_TASK_CREATED",
                aggregate_type="verification_task",
                aggregate_id=task.id,
                record=task,
            )
        except Exception:
            # A concurrent append can win the unique-task race.  The port does
            # not leak a database-specific integrity exception into this use
            # case; re-raise if no equivalent task is visible afterwards.
            existing = self._open_task(episode_id, belief_id, method)
            if existing is None:
                raise
            return VerificationResult(existing, False, ())
        return VerificationResult(task, True, (event.id,))

    def passive_cross_source_count(
        self,
        belief: Belief,
        beliefs: list[Belief],
        sources: dict[str, Source],
    ) -> int:
        threshold = self.settings.ingestion.near_duplicate_threshold
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
        events = self._writer.append_events(
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

    def _open_task(
        self, episode_id: str, belief_id: str, method: VerificationMethod
    ) -> VerificationTask | None:
        return next(
            (
                task
                for task in self._reader.list_verification_tasks(episode_id, state="open")
                if task.belief_id == belief_id and task.method is method
            ),
            None,
        )
