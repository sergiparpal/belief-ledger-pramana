"""Group recorded model calls and report identical inputs that produced different outputs.

This is what turns "the model component is non-deterministic" from a caveat into an audit. The
digests are already in the event log; this reads them back and groups by
`(prompt_hash, input_hash)`, reporting every group holding more than one distinct `output_hash`.

It reads events rather than a projection on purpose. The event log is authoritative, the query is
an operator command rather than a hot path, and a projection would mean a schema migration for
something that answers a question about history.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..events import isoformat_utc
from ..models import Event

ATTRIBUTION_KIND = "LLM_CALL_ATTRIBUTION"


@dataclass(frozen=True, slots=True)
class RecordedCall:
    event_id: str
    episode_id: str
    purpose: str
    provider: str
    model: str
    prompt_hash: str
    input_hash: str
    output_hash: str | None
    sampling: dict[str, Any]
    outcome: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class DivergentGroup:
    """One input that the model answered in more than one way."""

    prompt_hash: str
    input_hash: str
    purpose: str
    output_hashes: tuple[str, ...]
    calls: tuple[RecordedCall, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_hash": self.prompt_hash,
            "input_hash": self.input_hash,
            "purpose": self.purpose,
            "distinct_outputs": len(self.output_hashes),
            "output_hashes": list(self.output_hashes),
            "calls": [
                {
                    "event_id": call.event_id,
                    "episode_id": call.episode_id,
                    "model": call.model,
                    "provider": call.provider,
                    "output_hash": call.output_hash,
                    "sampling": dict(call.sampling),
                    "timestamp": call.timestamp,
                }
                for call in self.calls
            ],
        }


def recorded_calls(events: Iterable[Event]) -> list[RecordedCall]:
    calls: list[RecordedCall] = []
    for event in events:
        if event.kind != ATTRIBUTION_KIND:
            continue
        record = event.payload.get("record")
        # Event payloads come back frozen, and FrozenDict is a Mapping rather than a dict.
        if not isinstance(record, Mapping):
            continue
        output_hash = record.get("output_hash")
        sampling = record.get("sampling")
        calls.append(
            RecordedCall(
                event_id=event.id,
                episode_id=str(record.get("episode_id", event.episode_id)),
                purpose=str(record.get("purpose", "")),
                provider=str(record.get("provider", "")),
                model=str(record.get("model", "")),
                prompt_hash=str(record.get("prompt_hash", "")),
                input_hash=str(record.get("input_hash", "")),
                output_hash=None if output_hash is None else str(output_hash),
                sampling=dict(sampling) if isinstance(sampling, Mapping) else {},
                outcome=str(record.get("outcome", "")),
                timestamp=isoformat_utc(event.timestamp),
            )
        )
    return calls


def divergent_groups(calls: Sequence[RecordedCall]) -> list[DivergentGroup]:
    """Every `(prompt_hash, input_hash)` group with more than one distinct output.

    Failed calls carry no output and are excluded. An error is not a divergent answer — it is the
    absence of one — and counting it as a distinct output would report every transient timeout as
    model non-determinism.
    """
    grouped: dict[tuple[str, str], list[RecordedCall]] = {}
    for call in calls:
        if call.output_hash is None:
            continue
        grouped.setdefault((call.prompt_hash, call.input_hash), []).append(call)

    groups: list[DivergentGroup] = []
    for (prompt, input_hash), members in sorted(grouped.items()):
        distinct = sorted({str(call.output_hash) for call in members})
        if len(distinct) < 2:
            continue
        ordered = tuple(sorted(members, key=lambda call: (call.timestamp, call.event_id)))
        groups.append(
            DivergentGroup(
                prompt_hash=prompt,
                input_hash=input_hash,
                purpose=ordered[0].purpose,
                output_hashes=tuple(distinct),
                calls=ordered,
            )
        )
    return groups


def divergence_report(events: Iterable[Event]) -> dict[str, Any]:
    calls = recorded_calls(events)
    groups = divergent_groups(calls)
    return {
        "recorded_calls": len(calls),
        "distinct_inputs": len({(call.prompt_hash, call.input_hash) for call in calls}),
        "divergent_groups": len(groups),
        "groups": [group.as_dict() for group in groups],
    }
