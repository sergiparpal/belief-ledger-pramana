"""Injected deterministic dependencies for host-neutral runtime paths."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .ids import id_prefix, new_id


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class MonotonicClockPort(Protocol):
    def now(self) -> float: ...


class IdentityPort(Protocol):
    def new(self, kind: str) -> str: ...


class TokenPort(Protocol):
    def issue(self, nbytes: int = 32) -> str: ...


@dataclass(frozen=True, slots=True)
class SamplingPolicy:
    """Sampling parameters a caller asks the host to apply.

    `temperature=0.0` is the default because a component verdict is a judgement the ledger records
    and replays, not a generation. It cannot make a host deterministic — batching, model routing
    and provider-side changes are all outside this process — so the policy is recorded on every
    call alongside the input and output digests, and `llm.divergence` reports identical inputs that
    produced different outputs. Reducing non-determinism and detecting it are separate jobs; this
    type only does the first.

    There is no `seed` field. `StructuredModelPort` does not accept one, and a knob that is
    recorded but never applied would misrepresent what was asked of the provider.
    """

    temperature: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)):
            raise ValueError("sampling temperature must be a number")
        if not 0.0 <= float(self.temperature) <= 2.0:
            raise ValueError("sampling temperature must be between 0.0 and 2.0")


@dataclass(frozen=True, slots=True)
class StructuredModelRequest:
    schema_version: int
    purpose: str
    instructions: str
    text: str
    json_schema: dict[str, Any]
    max_tokens: int
    timeout_seconds: float
    # Additive and defaulted, so an existing port implementation keeps working unchanged.
    sampling: SamplingPolicy = field(default_factory=SamplingPolicy)


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
        return new_id(kind)


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
        return f"{id_prefix(kind)}{count:016d}"


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


def system_dependencies() -> RuntimeDependencies:
    """Offline-safe production defaults with secure identities and permit tokens."""

    return RuntimeDependencies(
        SystemClock(),
        SystemMonotonicClock(),
        SecureIdentity(),
        SecureToken(),
        FakeStructuredModel(),
    )
