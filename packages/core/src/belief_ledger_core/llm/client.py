"""Structured-model wrapper with deterministic budgets, attribution, and reentrancy guard."""

from __future__ import annotations

import contextvars
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..dependencies import RuntimeDependencies, StructuredModelPort, StructuredModelRequest
from ..errors import LlmReservationError
from ..events import EventDraft, content_hash
from ..models import ComponentVerdict, LlmUsage
from ..store import LedgerStore

_IN_COMPONENT_CALL: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "belief_ledger_component_call", default=False
)


class LlmComponentError(RuntimeError):
    pass


class LlmBudgetError(LlmComponentError):
    """A component failure caused by an exhausted auditable budget."""

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
        model_port: StructuredModelPort,
        store: LedgerStore,
        config: dict[str, Any],
        dependencies: RuntimeDependencies,
    ) -> None:
        self._model_port = model_port
        self._store = store
        self._config = config
        self._dependencies = dependencies

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
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise LlmComponentError("max_tokens must be a positive integer")
        if _IN_COMPONENT_CALL.get():
            raise LlmComponentError("recursive component model call blocked")
        episode = self._store.get_episode(episode_id)
        if episode is None:
            raise LlmComponentError("episode does not exist")
        limits = self._config["verification"]
        # Reserve before invoking the host model.  Checking counters alone is
        # racy when multiple hooks run at once or another process shares the
        # ledger database.
        # A character is a conservative upper bound for token accounting across
        # the supported providers. Reserving that upper bound prevents parallel
        # calls from overspending an episode before actual usage is reported.
        estimated_input = max(1, len(instructions) + len(text) + len(str(schema)))
        try:
            reservation_id = self._store.reserve_llm_budget(
                episode_id,
                episode.current_turn,
                input_tokens=estimated_input,
                output_tokens=max_tokens,
                max_calls_turn=int(limits["max_llm_calls_per_turn"]),
                max_calls_episode=int(limits["max_llm_calls_per_episode"]),
                max_input_tokens_episode=int(limits["max_input_tokens_per_episode"]),
                max_output_tokens_episode=int(limits["max_output_tokens_per_episode"]),
            )
        except LlmReservationError as exc:
            raise LlmBudgetError(str(exc)) from exc

        token = _IN_COMPONENT_CALL.set(True)
        started = self._dependencies.monotonic.now()
        provider = ""
        model = ""
        input_tokens = estimated_input
        output_tokens = max_tokens
        cost: float | None = None
        outcome = "error"
        parsed: Any = None
        caught: Exception | None = None
        try:
            result = self._model_port.complete(
                StructuredModelRequest(
                    1,
                    purpose,
                    instructions,
                    text,
                    schema,
                    max_tokens,
                    float(limits["structured_timeout_seconds"]),
                )
            )
            if result.schema_version != 1:
                raise ValueError("structured model result schema_version is invalid")
            provider = _bounded_label(result.provider, "provider")
            model = _bounded_label(result.model, "model")
            input_tokens = _valid_input_tokens(result.input_tokens, estimated_input)
            output_tokens = _valid_output_tokens(result.output_tokens, max_tokens)
            cost = _valid_cost(result.cost_usd)
            parsed = validator(result.parsed)
            outcome = "success"
        except Exception as exc:  # attribution is persisted before the stable wrapper is raised
            caught = exc
            outcome = type(exc).__name__
        finally:
            _IN_COMPONENT_CALL.reset(token)

        latency_ms = max(0, int((self._dependencies.monotonic.now() - started) * 1_000))
        usage_record = LlmUsage(
            id=self._dependencies.identity.new("usage"),
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
            id=self._dependencies.identity.new("verdict"),
            episode_id=episode_id,
            component=purpose.split(".")[-1],
            purpose=purpose,
            input_hash=content_hash(text),
            outcome=outcome,
            belief_id=None,
            detail={"schema_name": schema_name},
        )
        try:
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
        finally:
            self._store.release_llm_reservation(reservation_id)
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

    return EventDraft(kind, aggregate_type, aggregate_id, {"record": to_primitive(record)})


def _valid_input_tokens(value: Any, reservation: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return reservation
    return value


def _valid_output_tokens(value: Any, reservation: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return reservation
    return value


def _bounded_label(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 256
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError(f"provider {label} is invalid")
    return value


def _valid_cost(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("provider cost is invalid")
    cost = float(value)
    if not math.isfinite(cost) or cost < 0:
        raise ValueError("provider cost is invalid")
    return cost
