"""Hermes facade translation for the host-neutral structured-model port."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..dependencies import (
    StructuredModelPort,
    StructuredModelProviderError,
    StructuredModelRequest,
    StructuredModelResult,
)


class HermesStructuredModelPort(StructuredModelPort):
    def __init__(self, facade_getter: Callable[[], Any]) -> None:
        self._facade_getter = facade_getter

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        try:
            result = self._facade_getter().complete_structured(
                instructions=request.instructions,
                input=[{"type": "text", "text": request.text}],
                json_schema=request.json_schema,
                json_mode=True,
                schema_name=request.purpose.replace(".", "_")[:64],
                temperature=0.0,
                max_tokens=request.max_tokens,
                timeout=request.timeout_seconds,
                purpose=request.purpose,
            )
        except Exception as exc:
            raise StructuredModelProviderError(type(exc).__name__) from exc
        usage = getattr(result, "usage", None)
        raw_cost = getattr(usage, "cost_usd", None)
        return StructuredModelResult(
            1,
            getattr(result, "parsed", None),
            str(getattr(result, "provider", "")),
            str(getattr(result, "model", "")),
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
            float(raw_cost) if raw_cost is not None else None,
        )
