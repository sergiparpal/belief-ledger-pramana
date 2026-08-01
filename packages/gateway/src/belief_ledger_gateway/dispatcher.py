"""In-process dispatcher whose registry is private behind atomic permit consumption."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from belief_ledger_core import BeliefLedger, ToolDescriptor, ToolInvocation

Handler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class HandlerResult:
    schema_version: int
    executed: bool
    reason_code: str
    value: Any = None


class GatewayDispatcher:
    """Own registered effectful handlers and retrieve them only after permit consumption."""

    def __init__(self, ledger: BeliefLedger) -> None:
        self.ledger = ledger
        self._handlers: dict[tuple[str, str], Handler] = {}
        self._effectful: dict[tuple[str, str], bool] = {}
        self._descriptors: dict[tuple[str, str], ToolDescriptor] = {}

    @property
    def capability_profile(self) -> str:
        return self.ledger.effective_profile.value

    def register(
        self,
        descriptor: ToolDescriptor,
        handler: Handler,
        *,
        effectful: bool,
    ) -> None:
        key = (descriptor.namespace, descriptor.name)
        if not descriptor.name or key in self._handlers:
            raise ValueError("tool registration must be non-empty and unique")
        classified = self.ledger.inventory((descriptor,), complete=True)[0]
        if classified.reason_code != "POLICY_MATCHED":
            raise ValueError(classified.reason_code)
        policy = self.ledger.manifest.match(descriptor.name, descriptor.namespace)
        if policy is None or policy.effectful is not effectful:
            raise ValueError("tool effect classification disagrees with its policy")
        self._descriptors[key] = descriptor
        self._effectful[key] = effectful
        self._handlers[key] = handler

    def dispatch(self, episode_id: str, invocation: ToolInvocation) -> HandlerResult:
        key = (invocation.namespace, invocation.name)
        effectful = self._effectful.get(key)
        if effectful is None:
            return HandlerResult(1, False, "UNKNOWN_TOOL")
        descriptor = self._descriptors[key]
        try:
            descriptor.validate_arguments(invocation.arguments_dict())
        except ValueError:
            return HandlerResult(1, False, "INVALID_ARGUMENTS")
        if effectful:
            authorization = self.ledger.evaluate_action(episode_id, invocation)
            if authorization.permit is None:
                return HandlerResult(1, False, authorization.reason_code)
            consumed = self.ledger.consume_permission(authorization.permit, invocation)
            if not consumed.consumed:
                return HandlerResult(1, False, consumed.reason_code)
        handler = self._handlers.get(key)
        if handler is None:
            return HandlerResult(1, False, "HANDLER_UNAVAILABLE")
        try:
            return HandlerResult(1, True, "DISPATCHED", handler(invocation.arguments_dict()))
        except Exception as exc:
            return HandlerResult(1, False, "HANDLER_ERROR", type(exc).__name__)
