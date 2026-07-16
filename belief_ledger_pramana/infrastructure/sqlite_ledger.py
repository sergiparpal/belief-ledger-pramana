"""SQLite implementations of focused ledger ports.

The existing ``LedgerStore`` remains the backwards-compatible facade.  These
adapters make its read, write, budget, and maintenance responsibilities
explicit at application composition points, allowing an alternate event store
to be introduced without changing use cases.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..events import EventDraft
from ..models import (
    Belief,
    Conflict,
    DefeatEdge,
    Episode,
    Event,
    RetractionNotice,
    Source,
    VerificationTask,
)
from ..store import LedgerStore, PurgeResult, ReplayResult


@dataclass(frozen=True, slots=True)
class SqliteEventWriter:
    """SQLite event append adapter preserving one-batch atomicity."""

    store: LedgerStore

    def append_events(
        self,
        episode_id: str,
        drafts: Sequence[EventDraft],
        *,
        correlation: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        require_open_verification_task_id: str | None = None,
    ) -> list[Event]:
        return self.store.append_events(
            episode_id,
            drafts,
            correlation=correlation,
            idempotency_key=idempotency_key,
            require_open_verification_task_id=require_open_verification_task_id,
        )

    def append_record(
        self,
        episode_id: str,
        *,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        record: Any,
        correlation: dict[str, str] | None = None,
    ) -> Event:
        return self.store.append_record(
            episode_id,
            kind=kind,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            record=record,
            correlation=correlation,
        )


@dataclass(frozen=True, slots=True)
class SqliteLedgerReader:
    """Read-model adapter used by application services."""

    store: LedgerStore

    def get_episode(self, episode_id: str) -> Episode | None:
        return self.store.get_episode(episode_id)

    def get_belief(self, belief_id: str) -> Belief | None:
        return self.store.get_belief(belief_id)

    def get_beliefs(self, belief_ids: Iterable[str]) -> dict[str, Belief]:
        return self.store.get_beliefs(belief_ids)

    def get_source(self, source_id: str) -> Source | None:
        return self.store.get_source(source_id)

    def list_beliefs(self, episode_id: str, **kwargs: Any) -> list[Belief]:
        return self.store.list_beliefs(episode_id, **kwargs)

    def list_sources(self, episode_id: str) -> list[Source]:
        return self.store.list_sources(episode_id)

    def list_conflicts(self, episode_id: str, **kwargs: Any) -> list[Conflict]:
        return self.store.list_conflicts(episode_id, **kwargs)

    def list_defeats(self, episode_id: str) -> list[DefeatEdge]:
        return self.store.list_defeats(episode_id)

    def list_retractions(self, episode_id: str, **kwargs: Any) -> list[RetractionNotice]:
        return self.store.list_retractions(episode_id, **kwargs)

    def get_verification_task(self, task_id: str) -> VerificationTask | None:
        return self.store.get_verification_task(task_id)

    def list_verification_tasks(
        self, episode_id: str, *, state: str | None = "open"
    ) -> list[VerificationTask]:
        return self.store.list_verification_tasks(episode_id, state=state)

    def events(self, episode_id: str | None = None) -> list[Event]:
        return self.store.events(episode_id)

    def fts_belief_ids(self, episode_id: str, query: str, *, limit: int = 200) -> tuple[str, ...]:
        return self.store.fts_belief_ids(episode_id, query, limit=limit)


@dataclass(frozen=True, slots=True)
class SqliteLlmBudgetLedger:
    """Atomic LLM reservation and audit append adapter."""

    store: LedgerStore

    def get_episode(self, episode_id: str) -> Episode | None:
        return self.store.get_episode(episode_id)

    def reserve_llm_budget(
        self,
        episode_id: str,
        turn_number: int,
        *,
        input_tokens: int,
        output_tokens: int,
        max_calls_turn: int,
        max_calls_episode: int,
        max_input_tokens_episode: int,
        max_output_tokens_episode: int,
    ) -> str:
        return self.store.reserve_llm_budget(
            episode_id,
            turn_number,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            max_calls_turn=max_calls_turn,
            max_calls_episode=max_calls_episode,
            max_input_tokens_episode=max_input_tokens_episode,
            max_output_tokens_episode=max_output_tokens_episode,
        )

    def release_llm_reservation(self, reservation_id: str) -> None:
        self.store.release_llm_reservation(reservation_id)

    def append_events(
        self,
        episode_id: str,
        drafts: Sequence[EventDraft],
        *,
        correlation: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        require_open_verification_task_id: str | None = None,
    ) -> list[Event]:
        return self.store.append_events(
            episode_id,
            drafts,
            correlation=correlation,
            idempotency_key=idempotency_key,
            require_open_verification_task_id=require_open_verification_task_id,
        )

    def append_record(self, *args: Any, **kwargs: Any) -> Event:
        return self.store.append_record(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class SqliteLedgerMaintenance:
    """Operational/replay actions intentionally kept outside use cases."""

    store: LedgerStore

    def checkpoint(self) -> None:
        self.store.checkpoint()

    def replay(self) -> ReplayResult:
        return self.store.replay()

    def purge_episode(self, episode_id: str, *, confirmation: str) -> PurgeResult:
        return self.store.purge_episode(episode_id, confirmation=confirmation)
