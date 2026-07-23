from __future__ import annotations

import pytest
from belief_ledger_core.buffering import MemorySink, ResponseGate

BLOCK = b"BLOCKED [OUTPUT_NOT_ACCEPTED]"


def _gate(max_bytes: int = 100) -> ResponseGate:
    return ResponseGate(max_bytes=max_bytes, block_report=BLOCK.decode())


def test_sink_observes_zero_bytes_until_accepted() -> None:
    sink = MemorySink()
    gate = _gate()
    gate.append(0, "safe ")
    gate.append(1, "answer")
    assert sink.deliveries == []
    result = gate.finalize(lambda text: text == "safe answer", sink)
    assert result.accepted
    assert sink.deliveries == [b"safe answer"]
    assert gate.finalize(lambda text: True, sink).reason_code == "MULTIPLE_FINAL"


@pytest.mark.parametrize(
    "configure,reason",
    [
        (lambda gate: gate.append(1, b"late"), "INVALID_CHUNK_ORDER"),
        (lambda gate: gate.append(0, b"too-long"), "BUFFER_OVERFLOW"),
        (lambda gate: gate.append(0, b"\xff"), "INVALID_UTF8"),
        (lambda gate: gate.cancel(), "CANCELLED"),
    ],
)
def test_buffer_failures_deliver_only_block_report(configure, reason: str) -> None:
    gate = _gate(max_bytes=4)
    sink = MemorySink()
    configure(gate)
    result = gate.finalize(lambda text: True, sink)
    assert result.reason_code == reason
    assert sink.deliveries == [BLOCK]


def test_utf8_chunks_may_split_at_codepoint_boundaries() -> None:
    encoded = "café".encode()
    gate = _gate()
    gate.append(0, encoded[:4])
    gate.append(1, encoded[4:])
    sink = MemorySink()
    assert gate.finalize(lambda text: text == "café", sink).accepted
    assert sink.deliveries == [encoded]


def test_lint_block_and_exception_are_fail_closed() -> None:
    for lint, reason in (
        (lambda text: False, "LINT_BLOCKED"),
        (lambda text: (_ for _ in ()).throw(RuntimeError("boom")), "LINTER_ERROR"),
    ):
        gate = _gate()
        gate.append(0, "unsafe provisional text")
        sink = MemorySink()
        assert gate.finalize(lint, sink).reason_code == reason
        assert sink.deliveries == [BLOCK]


class _PrepareFailsOnce(MemorySink):
    def prepare(self, size: int) -> None:
        super().prepare(size)
        if len(self.prepared) == 1:
            raise RuntimeError("first preparation failed")


class _AlwaysPrepareFails(MemorySink):
    def prepare(self, size: int) -> None:
        raise RuntimeError("unavailable")


class _DeliveryFails(MemorySink):
    def deliver(self, payload: bytes) -> None:
        raise RuntimeError("unavailable")


def test_sink_failures_never_release_provisional_content() -> None:
    gate = _gate()
    gate.append(0, "provisional")
    once = _PrepareFailsOnce()
    result = gate.finalize(lambda text: True, once)
    assert result.reason_code == "SINK_PREPARE_FAILED"
    assert once.deliveries == [BLOCK]

    gate = _gate()
    gate.append(0, "provisional")
    assert gate.finalize(lambda text: True, _AlwaysPrepareFails()).delivered_bytes == 0

    gate = _gate()
    gate.append(0, "provisional")
    assert gate.finalize(lambda text: True, _DeliveryFails()).reason_code == "SINK_DELIVERY_FAILED"


def test_append_after_final_is_rejected_without_second_delivery() -> None:
    gate = _gate()
    sink = MemorySink()
    gate.append(0, "one")
    assert gate.finalize(lambda text: True, sink).accepted
    gate.append(1, "two")
    assert sink.deliveries == [b"one"]
