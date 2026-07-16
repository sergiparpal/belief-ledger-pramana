"""Application use case for compiling and maintaining ephemeral context."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import ConfigSnapshot
from ..context.render import RenderedContext, render_context
from ..context.select import select_beliefs
from ..events import EventDraft, utc_now
from ..ids import new_id
from ..ingestion.tool import redacted_content_hash
from ..models import Episode, Health
from ..ports import ContextReader, EventWriter


class ContextCompilationUseCase:
    """Compile an auditable, non-accumulative context view through ports."""

    def __init__(
        self,
        reader: ContextReader,
        writer: EventWriter,
        config: ConfigSnapshot,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._config = config
        self._settings = config.settings

    def compile(
        self,
        episode_id: str,
        episode: Episode,
        *,
        query: str,
        pending_tool_intent: str,
        recent_tool_result: str,
        request_id: str,
        ascii_only: bool,
        defer_component_work: bool,
        health: Health,
        injection_failed: bool,
        defer_maintenance: Callable[[str], None],
        promote_relevant: Callable[[str], Any],
        run_chain_audit: Callable[[str], Any],
        relabel: Callable[[], Any],
    ) -> RenderedContext:
        effective_query = "\n".join(
            item for item in (query, pending_tool_intent, recent_tool_result) if item
        )
        if defer_component_work:
            defer_maintenance(effective_query)
        else:
            promote_relevant(effective_query)
            run_chain_audit(effective_query)
        relabel()
        beliefs = self._reader.list_beliefs(episode_id)
        sources = {source.id: source for source in self._reader.list_sources(episode_id)}
        selection = select_beliefs(
            beliefs,
            sources,
            query=effective_query,
            conflicts=self._reader.list_conflicts(episode_id),
            retractions=self._reader.list_retractions(episode_id),
            retrieval_ids=(
                self._reader.fts_belief_ids(
                    episode_id,
                    effective_query,
                    limit=self._settings.context.max_beliefs * 4,
                )
                if self._settings.context.relevance == "fts5"
                else ()
            ),
            config=self._config.data,
        )
        resolved_request_id = request_id or new_id("event")
        rendered = render_context(
            selection,
            sources,
            config=self._config.data,
            health=Health.DEGRADED if injection_failed else health,
            request_id=resolved_request_id,
            ascii_only=ascii_only,
        )
        now = utc_now()
        self._writer.append_events(
            episode_id,
            [
                EventDraft(
                    "CONTEXT_COMPILED",
                    "episode",
                    episode_id,
                    {
                        "request_id": resolved_request_id,
                        "config_digest": self._config.digest,
                        "query_hash": redacted_content_hash(effective_query),
                        "truncated": rendered.truncated,
                        "rendered": [
                            {
                                "belief_id": belief_id,
                                "request_id": resolved_request_id,
                                "turn_number": episode.current_turn,
                                "rendered_at": now,
                            }
                            for belief_id in rendered.belief_ids
                        ],
                    },
                )
            ],
        )
        return rendered

    def run_deferred_maintenance(
        self,
        query: str,
        *,
        detect_deterministic_rebuts: Callable[[], Any],
        relabel: Callable[[], Any],
        complete_passive_tasks: Callable[[], object],
        promote_relevant: Callable[[str], Any],
        run_chain_audit: Callable[[str], Any],
    ) -> None:
        """Run optional model-assisted work outside the host callback loop."""

        detect_deterministic_rebuts()
        relabel()
        if complete_passive_tasks():
            relabel()
        promote_relevant(query)
        run_chain_audit(query)
        relabel()
