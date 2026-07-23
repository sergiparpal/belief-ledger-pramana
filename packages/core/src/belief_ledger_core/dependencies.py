"""Injected deterministic dependencies for host-neutral runtime paths."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class MonotonicClockPort(Protocol):
    def now(self) -> float: ...


class IdentityPort(Protocol):
    def new(self, kind: str) -> str: ...


class TokenPort(Protocol):
    def issue(self, nbytes: int = 32) -> str: ...


@dataclass(frozen=True, slots=True)
class StructuredModelRequest:
    schema_version: int
    purpose: str
    instructions: str
    text: str
    json_schema: dict[str, Any]
    max_tokens: int
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class StructuredModelResult:
    schema_version: int
    parsed: Any
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class StructuredModelError(RuntimeError):
    """Base stable structured-model failure."""


class StructuredModelTimeout(StructuredModelError):
    pass


class StructuredModelValidationError(StructuredModelError):
    pass


class StructuredModelProviderError(StructuredModelError):
    pass


class StructuredModelBudgetError(StructuredModelError):
    pass


class StructuredModelPort(Protocol):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult: ...


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    clock: ClockPort
    monotonic: MonotonicClockPort
    identity: IdentityPort
    token: TokenPort
    structured_model: StructuredModelPort


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SystemMonotonicClock:
    def now(self) -> float:
        return time.monotonic()


class SecureIdentity:
    def new(self, kind: str) -> str:
        return f"{kind}_{secrets.token_urlsafe(18)}"


class SecureToken:
    def issue(self, nbytes: int = 32) -> str:
        return secrets.token_urlsafe(nbytes)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("fixed clock value must be timezone-aware")
        self._value = value.astimezone(UTC)

    def now(self) -> datetime:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += timedelta(seconds=seconds)


class FixedMonotonicClock:
    def __init__(self, value: float = 0.0) -> None:
        self._value = value

    def now(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


class SequenceIdentity:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def new(self, kind: str) -> str:
        count = self._counts.get(kind, 0) + 1
        self._counts[kind] = count
        return f"{kind}_{count:04d}"


class SequenceToken:
    def __init__(self, values: Iterable[str] = ()) -> None:
        self._values = iter(values)
        self._count = 0

    def issue(self, nbytes: int = 32) -> str:
        del nbytes
        try:
            return next(self._values)
        except StopIteration:
            self._count += 1
            return f"deterministic-token-{self._count:04d}"


class FakeStructuredModel:
    def __init__(self, results: Iterable[StructuredModelResult] = ()) -> None:
        self._results = iter(results)
        self.requests: list[StructuredModelRequest] = []

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        self.requests.append(request)
        try:
            return next(self._results)
        except StopIteration as exc:
            raise StructuredModelProviderError("no deterministic result queued") from exc


class CallableStructuredModel:
    """Production adapter around an audited normalized provider callable."""

    def __init__(self, complete: Callable[[StructuredModelRequest], StructuredModelResult]) -> None:
        self._complete = complete

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        return self._complete(request)


def deterministic_dependencies() -> RuntimeDependencies:
    return RuntimeDependencies(
        FixedClock(datetime(2026, 7, 22, 12, 0, tzinfo=UTC)),
        FixedMonotonicClock(),
        SequenceIdentity(),
        SequenceToken(),
        FakeStructuredModel(),
    )
