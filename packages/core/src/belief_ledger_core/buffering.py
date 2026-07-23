"""Bounded high-stakes response buffering with fail-closed delivery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class DeliverySink(Protocol):
    def prepare(self, size: int) -> None: ...

    def deliver(self, payload: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class BufferResult:
    schema_version: int
    accepted: bool
    reason_code: str
    delivered_bytes: int


class ResponseGate:
    def __init__(self, *, max_bytes: int, block_report: str) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes
        self.block_report = block_report.encode("utf-8")
        self._buffer = bytearray()
        self._next_index = 0
        self._failed_reason: str | None = None
        self._finalized = False

    def append(self, index: int, chunk: str | bytes) -> None:
        if self._finalized:
            self._failed_reason = self._failed_reason or "MULTIPLE_FINAL"
            return
        if index != self._next_index:
            self._failed_reason = self._failed_reason or "INVALID_CHUNK_ORDER"
            return
        self._next_index += 1
        payload = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
        if len(self._buffer) + len(payload) > self.max_bytes:
            self._failed_reason = self._failed_reason or "BUFFER_OVERFLOW"
            return
        self._buffer.extend(payload)

    def cancel(self) -> None:
        self._failed_reason = self._failed_reason or "CANCELLED"

    def finalize(self, lint: Callable[[str], bool], sink: DeliverySink) -> BufferResult:
        if self._finalized:
            return BufferResult(1, False, "MULTIPLE_FINAL", 0)
        self._finalized = True
        reason = self._failed_reason
        text = ""
        if reason is None:
            try:
                text = bytes(self._buffer).decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                reason = "INVALID_UTF8"
        if reason is None:
            try:
                if not lint(text):
                    reason = "LINT_BLOCKED"
            except Exception:
                reason = "LINTER_ERROR"
        payload = self.block_report if reason else bytes(self._buffer)
        accepted = reason is None
        try:
            sink.prepare(len(payload))
        except Exception:
            if not accepted:
                return BufferResult(1, False, "SINK_PREPARE_FAILED", 0)
            reason = "SINK_PREPARE_FAILED"
            accepted = False
            payload = self.block_report
            try:
                sink.prepare(len(payload))
            except Exception:
                return BufferResult(1, False, reason, 0)
        try:
            sink.deliver(payload)
        except Exception:
            return BufferResult(1, False, "SINK_DELIVERY_FAILED", 0)
        return BufferResult(1, accepted, reason or "ACCEPTED", len(payload))


class MemorySink:
    def __init__(self) -> None:
        self.prepared: list[int] = []
        self.deliveries: list[bytes] = []

    def prepare(self, size: int) -> None:
        self.prepared.append(size)

    def deliver(self, payload: bytes) -> None:
        self.deliveries.append(payload)
