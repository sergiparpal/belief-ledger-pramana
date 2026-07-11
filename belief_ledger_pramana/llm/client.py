"""ctx.llm adapter with deterministic budgets, attribution, and reentrancy guard."""

from __future__ import annotations

import contextvars
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..events import content_hash
from ..ids import new_id
from ..models import ComponentVerdict, LlmUsage
from ..store import LedgerStore

_IN_COMPONENT_CALL: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "belief_ledger_component_call", default=False
)


class LlmBudgetError(RuntimeError):
    pass


class LlmComponentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StructuredCallResult:
    parsed: Any
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    event_ids: tuple[str, ...]


class HostLlmClient:
    def __init__(
        self,
        facade_getter: Callable[[], Any],
        store: LedgerStore,
        config: dict[str, Any],
    ) -> None:
        self._facade_getter = facade_getter
        self._store = store
        self._config = config

    def complete_structured(
        self,
        *,
        episode_id: str,
        purpose: str,
        instructions: str,
        text: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int,
        validator: Callable[[Any], Any],
    ) -> StructuredCallResult:
        if _IN_COMPONENT_CALL.get():
            raise LlmComponentError("recursive component model call blocked")
        episode = self._store.get_episode(episode_id)
        if episode is None:
            raise LlmComponentError("episode does not exist")
        limits = self._config["verification"]
        if self._store.llm_usage_count(episode_id, episode.current_turn) >= int(
            limits["max_llm_calls_per_turn"]
        ):
            raise LlmBudgetError("turn LLM call budget exhausted")
        if episode.llm_calls_used >= int(limits["max_llm_calls_per_episode"]):
            raise LlmBudgetError("episode LLM call budget exhausted")
        if episode.input_tokens_used >= int(limits["max_input_tokens_per_episode"]):
            raise LlmBudgetError("episode input-token budget exhausted")
        if episode.output_tokens_used >= int(limits["max_output_tokens_per_episode"]):
            raise LlmBudgetError("episode output-token budget exhausted")

        token = _IN_COMPONENT_CALL.set(True)
        started = time.monotonic()
        provider = ""
        model = ""
        input_tokens = 0
        output_tokens = 0
        cost: float | None = None
        outcome = "error"
        parsed: Any = None
        caught: Exception | None = None
        try:
            facade = self._facade_getter()
            result = facade.complete_structured(
                instructions=instructions,
                input=[{"type": "text", "text": text}],
                json_schema=schema,
                json_mode=True,
                schema_name=schema_name,
                temperature=0.0,
                max_tokens=max_tokens,
                timeout=float(limits["structured_timeout_seconds"]),
                purpose=purpose,
            )
            provider = str(getattr(result, "provider", ""))
            model = str(getattr(result, "model", ""))
            usage = getattr(result, "usage", None)
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            reported_cost = getattr(usage, "cost_usd", None)
            cost = float(reported_cost) if reported_cost is not None else None
            parsed = validator(getattr(result, "parsed", None))
            outcome = "success"
        except Exception as exc:  # attribution is persisted before the stable wrapper is raised
            caught = exc
            outcome = type(exc).__name__
        finally:
            _IN_COMPONENT_CALL.reset(token)

        latency_ms = int((time.monotonic() - started) * 1_000)
        usage_record = LlmUsage(
            id=new_id("usage"),
            episode_id=episode_id,
            purpose=purpose,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            latency_ms=latency_ms,
            turn_number=episode.current_turn,
            outcome=outcome,
        )
        verdict = ComponentVerdict(
            id=new_id("verdict"),
            episode_id=episode_id,
            component=purpose.split(".")[-1],
            purpose=purpose,
            input_hash=content_hash(text),
            outcome=outcome,
            belief_id=None,
            detail={"schema_name": schema_name},
        )
        events = self._store.append_events(
            episode_id,
            [
                _record_draft("LLM_USAGE_RECORDED", "llm_usage", usage_record.id, usage_record),
                _record_draft(
                    "COMPONENT_VERDICT_RECORDED",
                    "component_verdict",
                    verdict.id,
                    verdict,
                ),
            ],
        )
        if caught is not None:
            raise LlmComponentError(f"{purpose} failed: {type(caught).__name__}") from caught
        return StructuredCallResult(
            parsed,
            provider,
            model,
            input_tokens,
            output_tokens,
            tuple(event.id for event in events),
        )


def _record_draft(kind: str, aggregate_type: str, aggregate_id: str, record: Any) -> Any:
    from ..events import to_primitive
    from ..store import EventDraft

    return EventDraft(kind, aggregate_type, aggregate_id, {"record": to_primitive(record)})
