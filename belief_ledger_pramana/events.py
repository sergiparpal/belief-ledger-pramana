"""Canonical event encoding and SHA-256 hash chaining."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .ids import new_id
from .models import Event


@dataclass(frozen=True, slots=True)
class EventDraft:
    """A storage-neutral description of an event to append atomically.

    Application services create drafts without knowing how a particular event
    store sequences, authenticates, or projects them.  The SQLite store
    remains one adapter that accepts this contract.
    """

    kind: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    causal_event_id: str | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("serialized datetime is not timezone-aware")
    return parsed.astimezone(UTC)


def to_primitive(value: Any) -> Any:
    """Convert supported records into canonical JSON-shaped values."""

    if dataclasses.is_dataclass(value):
        return {
            field.name: to_primitive(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return isoformat_utc(value)
    if isinstance(value, tuple):
        return [to_primitive(item) for item in value]
    if isinstance(value, list):
        return [to_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def content_hash(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def compute_event_hash(previous_hash: str, event_without_hash: dict[str, Any]) -> str:
    material = (
        previous_hash.encode("ascii") + b"\x00" + canonical_json(event_without_hash).encode("utf-8")
    )
    return hashlib.sha256(material).hexdigest()


def compute_event_auth(key: bytes, event_id: str, event_hash: str) -> str:
    """Authenticate one chained event with a key kept outside SQLite."""

    if len(key) < 32:
        raise ValueError("ledger integrity key must contain at least 256 bits")
    material = event_id.encode("utf-8") + b"\x00" + event_hash.encode("ascii")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def build_event(
    *,
    seq: int,
    episode_id: str,
    kind: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    previous_hash: str,
    correlation: dict[str, str] | None = None,
    causal_event_id: str | None = None,
    timestamp: datetime | None = None,
    event_id: str | None = None,
) -> Event:
    """Build a canonical event whose hash commits to all preceding fields."""

    event_id = event_id or new_id("event")
    timestamp = timestamp or utc_now()
    body = {
        "seq": seq,
        "id": event_id,
        "episode_id": episode_id,
        "timestamp": timestamp,
        "kind": kind,
        "schema_version": 1,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "correlation": correlation or {},
        "causal_event_id": causal_event_id,
        "payload": payload,
        "previous_hash": previous_hash,
    }
    event_hash = compute_event_hash(previous_hash, body)
    return Event(
        seq=seq,
        id=event_id,
        episode_id=episode_id,
        timestamp=timestamp,
        kind=kind,
        schema_version=1,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation=correlation or {},
        causal_event_id=causal_event_id,
        payload=payload,
        previous_hash=previous_hash,
        event_hash=event_hash,
    )
