"""Application entry point for pre-action policy evaluation."""

from __future__ import annotations

from typing import Any

from ..gate.decision import ActionGate
from ..models import GateDecision, Stakes


class ActionEvaluationUseCase:
    """Expose the action-gate use case without leaking its implementation."""

    def __init__(self, gate: ActionGate) -> None:
        self._gate = gate

    def execute(
        self,
        episode_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        description: str = "",
        action_stakes: Stakes | None = None,
    ) -> GateDecision:
        return self._gate.evaluate(
            episode_id,
            tool_name,
            args,
            description=description,
            action_stakes=action_stakes,
        )
