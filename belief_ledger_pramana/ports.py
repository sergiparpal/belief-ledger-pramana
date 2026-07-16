"""Application-facing ports for infrastructure and host integrations.

These protocols intentionally describe small client-specific capabilities.
`LedgerStore` satisfies them structurally today, while a future store adapter
can do the same without changing the application or domain packages.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from .events import EventDraft
from .models import (
    Belief,
    Conflict,
    DefeatEdge,
    Episode,
    Event,
    RetractionNotice,
    Source,
    VerificationTask,
)


class EventWriter(Protocol):
    """Append an indivisible batch of domain event drafts."""

    def append_events(
        self,
        episode_id: str,
        drafts: Sequence[EventDraft],
        *,
        correlation: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        require_open_verification_task_id: str | None = None,
    ) -> list[Event]: ...

    def append_record(
        self,
        episode_id: str,
        *,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        record: Any,
        correlation: dict[str, str] | None = None,
    ) -> Event: ...


class ActionGateReader(Protocol):
    """Read model required to evaluate an action policy."""

    def get_episode(self, episode_id: str) -> Episode | None: ...

    def list_beliefs(self, episode_id: str, **kwargs: Any) -> list[Belief]: ...

    def list_sources(self, episode_id: str) -> list[Source]: ...

    def list_conflicts(self, episode_id: str, **kwargs: Any) -> list[Conflict]: ...


class VerificationTaskReader(Protocol):
    """Read model required to schedule and complete verification."""

    def get_verification_task(self, task_id: str) -> VerificationTask | None: ...

    def list_verification_tasks(
        self, episode_id: str, *, state: str | None = "open"
    ) -> list[VerificationTask]: ...


class ActionGateLedger(ActionGateReader, EventWriter, Protocol):
    """Complete, minimal ledger contract used by the action-gate use case."""


class VerificationLedger(VerificationTaskReader, EventWriter, Protocol):
    """Complete, minimal ledger contract used by verification scheduling."""


class LedgerQueryReader(Protocol):
    """Read model for user-facing ledger query and explanation use cases."""

    def get_belief(self, belief_id: str) -> Belief | None: ...

    def get_beliefs(self, belief_ids: Iterable[str]) -> dict[str, Belief]: ...

    def get_source(self, source_id: str) -> Source | None: ...

    def list_beliefs(self, episode_id: str, **kwargs: Any) -> list[Belief]: ...

    def list_sources(self, episode_id: str) -> list[Source]: ...

    def list_defeats(self, episode_id: str) -> list[DefeatEdge]: ...

    def list_verification_tasks(
        self, episode_id: str, *, state: str | None = "open"
    ) -> list[VerificationTask]: ...

    def events(self, episode_id: str | None = None) -> list[Event]: ...


class ContextReader(Protocol):
    """Read model required to compile a contextual ledger view."""

    def list_beliefs(self, episode_id: str, **kwargs: Any) -> list[Belief]: ...

    def list_sources(self, episode_id: str) -> list[Source]: ...

    def list_conflicts(self, episode_id: str, **kwargs: Any) -> list[Conflict]: ...

    def list_retractions(self, episode_id: str, **kwargs: Any) -> list[RetractionNotice]: ...

    def fts_belief_ids(
        self, episode_id: str, query: str, *, limit: int = 200
    ) -> tuple[str, ...]: ...


class LlmBudgetLedger(EventWriter, Protocol):
    """Atomic LLM budget accounting and the append-only audit log."""

    def get_episode(self, episode_id: str) -> Episode | None: ...

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
    ) -> str: ...

    def release_llm_reservation(self, reservation_id: str) -> None: ...


class HostLlmFacade(Protocol):
    """Small audited subset of the Hermes model facade used by this plugin."""

    def complete_structured(self, **kwargs: Any) -> Any: ...


class EpisodeLifecycleStore(Protocol):
    """Operational store actions retained by the process-local runtime."""

    def get_episode(self, episode_id: str) -> Episode | None: ...

    def checkpoint(self) -> None: ...


__all__ = [
    "ActionGateLedger",
    "ActionGateReader",
    "ContextReader",
    "EpisodeLifecycleStore",
    "EventWriter",
    "HostLlmFacade",
    "LedgerQueryReader",
    "LlmBudgetLedger",
    "VerificationLedger",
    "VerificationTaskReader",
]
