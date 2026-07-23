"""Audited per-provider-request middleware adapter."""

from __future__ import annotations

import logging
from typing import Any

from ..context.inject import ContextInjectionError
from ..events import content_hash
from ..runtime import PluginRuntime

logger = logging.getLogger(__name__)


class LlmRequestMiddleware:
    def __init__(self, runtime: PluginRuntime) -> None:
        self.runtime = runtime

    def __call__(self, *, request: dict[str, Any], **kwargs: Any) -> dict[str, Any] | None:
        if self.runtime.compatibility.mode.value != "full":
            return None
        service = self.runtime.service(**kwargs)
        request_id = str(kwargs.get("api_request_id") or "")
        try:
            rendered = service.compile_context(
                query=self.runtime.query_for(service.episode_id),
                request_id=request_id,
                defer_component_work=self.runtime.in_running_event_loop(),
            )
            result = self.runtime.injector.inject(
                request,
                api_mode=str(kwargs.get("api_mode") or ""),
                context=rendered.text,
                binding=(
                    f"{request_id or f'{service.episode_id}:{service.episode.current_turn}'}:"
                    f"{content_hash(rendered.text)}"
                ),
            )
            self.runtime.clear_injection_failure(service.episode_id)
            if not result.changed:
                return None
            return {
                "request": result.request,
                "source": "belief-ledger-pramana",
                "reason": "epistemic-context",
            }
        except Exception as exc:
            self.runtime.mark_injection_failure(service.episode_id, type(exc).__name__)
            try:
                service.lifecycle.context_injection_failed(
                    service.episode_id,
                    request_id or content_hash(str(kwargs.get("api_mode") or "")),
                    str(kwargs.get("api_mode") or ""),
                    type(exc).__name__,
                )
            except Exception:
                logger.exception("failed to record context injection failure")
            if isinstance(exc, ContextInjectionError):
                return None
            return None
