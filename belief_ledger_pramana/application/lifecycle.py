"""Auditable application events emitted at host-lifecycle boundaries."""

from __future__ import annotations

from typing import Any

from ..events import EventDraft
from ..ports import EventWriter


class LifecycleEventRecorder:
    """Record lifecycle facts without exposing event-store mechanics to adapters."""

    def __init__(self, writer: EventWriter) -> None:
        self._writer = writer

    def record(
        self,
        episode_id: str,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        correlation: dict[str, str] | None = None,
    ) -> tuple[str, ...]:
        events = self._writer.append_events(
            episode_id,
            [EventDraft(kind, aggregate_type, aggregate_id, payload)],
            correlation=correlation,
        )
        return tuple(event.id for event in events)

    def context_injection_failed(
        self, episode_id: str, request_id: str, api_mode: str, reason: str
    ) -> tuple[str, ...]:
        return self.record(
            episode_id,
            "CONTEXT_INJECTION_FAILED",
            "request",
            request_id,
            {"api_mode": api_mode, "reason": reason},
        )
