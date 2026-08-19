"""Structured contradiction verdicts, with the ingestion clock pinned.

Both claims here are ingested through `ingest_user_message`, which stamps `observed_at` from
`utc_now()`. Since [ADR 0011](../../docs/adr/0011-unconditional-recency-key.md) made `recency_rank`
unconditional, that stamp decides the outcome: two user claims from one sender tie on integrity,
type, reliability and specificity, so recency is the only key left to settle the contest.

`priority_trace` computed the key as `int(observed_at.timestamp())` when this test was written,
i.e. truncated to whole seconds, so two ingestions a few milliseconds apart tied *only* when they
landed inside the same wall-clock second. Reading the real clock left the assertion below to grid
alignment — the same commit produced `pending`/`pending` on one CI run and `out`/`in` on another,
because the second run happened to straddle a second boundary.

[ADR 0016](../../docs/adr/0016-full-precision-recency-key.md) removed that truncation: the key is
now whole microseconds, so two real-clock ingestions milliseconds apart always resolve and never
tie. The pinned clock stays regardless. It is what makes the saṃśaya case below constructible at
all — that case needs one identical timestamp, which no real clock will hand out — and pinning both
regimes explicitly is what keeps this test a statement about the classifier rather than about
whatever the engine's granularity happens to be.

What does not depend on timing, and is the subject the test names: the classifier verdict produces
two REBUT edges and an R5 belief either way.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from belief_ledger_pramana.models import Status
from belief_ledger_pramana.runtime import episode_service

_BASE = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)


def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    """Freeze the ingestion clock and hand back a setter for the next stamp.

    `observed_at` reaches a belief through `Evidence`, which `ingest_user_message` builds itself;
    there is no clock seam on `PluginRuntime` to inject, so the module-level `utc_now` is patched.
    """

    current = {"value": _BASE}
    monkeypatch.setattr(episode_service, "utc_now", lambda: current["value"])

    def set_now(value: datetime) -> None:
        current["value"] = value

    return set_now


def _rebut_verdict() -> dict[str, object]:
    return {
        "outcome": "rebut",
        "left_scope": {},
        "right_scope": {},
        "basis": "healthy and down are incompatible runtime states",
    }


def _ingest_contradicting_pair(
    runtime, fake_ctx, set_now: Callable[[datetime], None], second_stamp: datetime
):
    """Ingest "Atlas is healthy" then "Atlas is down", the latter at `second_stamp`."""

    set_now(_BASE)
    service = runtime.begin_turn(
        session_id="s",
        turn_id="t1",
        user_message="Service Atlas is healthy.",
        sender_id="u",
    )
    service.ingest_user_message(
        "Service Atlas is healthy.", session_id="s", turn_id="t1", sender_id="u"
    )

    set_now(second_stamp)
    fake_ctx.llm.queue(_rebut_verdict())
    runtime.begin_turn(
        session_id="s",
        turn_id="t2",
        user_message="Service Atlas is down.",
        sender_id="u",
    )
    service = runtime.service(session_id="s")
    service.ingest_user_message(
        "Service Atlas is down.", session_id="s", turn_id="t2", sender_id="u"
    )
    return service


def _claims(service) -> dict[str, Status]:
    return {
        belief.content: belief.status
        for belief in service.store.list_beliefs(service.episode_id)
        if belief.content in {"Service Atlas is healthy", "Service Atlas is down"}
    }


def _assert_rebut_and_r5(service) -> None:
    """The classifier's structural output, which no timing regime changes."""

    assert len(service.store.list_defeats(service.episode_id)) == 2
    verdicts = [
        event.payload["record"]
        for event in service.store.events(service.episode_id)
        if event.kind == "COMPONENT_VERDICT_RECORDED"
        and event.payload["record"]["component"] == "contradiction_classifier"
    ]
    assert verdicts and verdicts[-1]["belief_id"].startswith("b_")


def test_structured_contradiction_verdict_creates_rebut_and_r5_belief(
    runtime, fake_ctx, monkeypatch
) -> None:
    """A fresher claim defeats the stale one it rebuts — ADR 0011's stated consequence.

    Two turns thirty seconds apart is the ordinary shape of this scenario, and the ADR's
    "Consequences" section says such a pair must resolve to one IN and one OUT rather than two
    PENDING. Before the clock was pinned this test asserted the pre-ADR-0011 answer and passed only
    because both ingestions usually landed in one second.
    """

    set_now = _pin_clock(monkeypatch)
    service = _ingest_contradicting_pair(runtime, fake_ctx, set_now, _BASE.replace(second=30))

    claims = _claims(service)
    assert len(claims) == 2
    assert claims["Service Atlas is down"] is Status.IN
    assert claims["Service Atlas is healthy"] is Status.OUT
    _assert_rebut_and_r5(service)


def test_structured_contradiction_at_one_timestamp_is_samsaya(
    runtime, fake_ctx, monkeypatch
) -> None:
    """Equal timestamps still tie on all five keys, so both claims go PENDING.

    ADR 0011 keeps saṃśaya for exactly this case: recency settles a contest only when the two
    observations actually differ in age. The unit-level control is
    `test_the_same_pair_at_one_timestamp_is_still_pending`; this pins the same rule through the
    full ingestion path, which is where the wall clock used to decide it.
    """

    set_now = _pin_clock(monkeypatch)
    service = _ingest_contradicting_pair(runtime, fake_ctx, set_now, _BASE)

    claims = _claims(service)
    assert len(claims) == 2
    assert all(status is Status.PENDING for status in claims.values())
    _assert_rebut_and_r5(service)
