"""Recency as an unconditional, full-precision priority key (ADR 0011, ADR 0016).

Before this change `priority_trace` computed recency only for FAST and LIVE beliefs and left it at
zero for everything else. Two SLOW or STABLE beliefs that differed only in age therefore tied on
all five keys, and an all-key tie is saṃśaya: both go to PENDING with a conflict and a verification
task. PENDING has no active exit, so "the fresher one is more likely right" was being converted
into an unbounded queue.

Recency stays the fifth and last key. It can only settle a contest that integrity, type,
reliability and specificity all left tied, which bounds the blast radius by construction.

ADR 0016 then removed the second-granularity truncation. The key was `int(observed_at.timestamp())`,
so whether two beliefs tied depended on where the second boundary fell rather than on how far apart
they were: 2 ms across a boundary resolved, 998 ms inside one second went to saṃśaya. The key is now
whole microseconds, which is `datetime`'s own resolution, so a tie means one instant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from belief_ledger_pramana.config import packaged_yaml
from belief_ledger_pramana.engine.defeat import relabel
from belief_ledger_pramana.engine.priority import compare_priority, priority_trace
from belief_ledger_pramana.models import (
    Belief,
    DefeatEdge,
    DefeatKind,
    EvidenceRef,
    IngestionSupport,
    Integrity,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    Stakes,
    Status,
)

CONFIG = packaged_yaml("defaults.yaml")
OLDER = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
FRESHER = OLDER + timedelta(hours=6)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _micros(value: datetime) -> int:
    """The expected rank, derived independently of the engine's own arithmetic."""
    delta = value - EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _source(
    identifier: str = "src_recency",
    *,
    integrity: Integrity = Integrity.TRUSTED,
    competence: float = 0.5,
) -> Source:
    return Source(
        identifier,
        "ep_recency",
        SourceKind.TOOL,
        integrity,
        identifier,
        f"{identifier}.example",
        {"general": competence},
    )


def _belief(
    identifier: str,
    source: Source,
    observed_at: datetime,
    *,
    perishability: Perishability = Perishability.SLOW,
) -> Belief:
    return Belief(
        identifier,
        "ep_recency",
        f"claim {identifier}",
        f"claim {identifier}",
        Pramana.PRATYAKSHA,
        source.id,
        (EvidenceRef(f"ev_{identifier}"),),
        (),
        {},
        perishability,
        observed_at,
        Stakes.LOW,
        Status.IN,
        Status.IN,
    )


def _support(belief: Belief) -> IngestionSupport:
    """A basic belief is OUT without a live ingestion support, whatever defeat decides."""
    return IngestionSupport(
        f"sup_{belief.id}", belief.episode_id, belief.id, belief.evidence[0].evidence_id, {}
    )


@pytest.mark.parametrize(
    "perishability",
    [Perishability.SLOW, Perishability.STABLE, Perishability.FAST, Perishability.LIVE],
    ids=lambda item: item.value,
)
def test_recency_is_computed_for_every_perishability_class(
    perishability: Perishability,
) -> None:
    source = _source()
    trace = priority_trace(
        _belief("b", source, FRESHER, perishability=perishability), source, CONFIG
    )

    assert trace.recency_rank == _micros(FRESHER)


@pytest.mark.parametrize(
    "perishability",
    [Perishability.SLOW, Perishability.STABLE],
    ids=lambda item: item.value,
)
def test_the_fresher_of_two_otherwise_identical_beliefs_wins(
    perishability: Perishability,
) -> None:
    source = _source()
    fresher = _belief("b_fresh", source, FRESHER, perishability=perishability)
    older = _belief("b_old", source, OLDER, perishability=perishability)
    sources = {source.id: source}

    comparison = compare_priority(fresher, older, sources, CONFIG)

    assert comparison.decisive_field == "recency"
    assert comparison.result == 1
    assert compare_priority(older, fresher, sources, CONFIG).result == -1


SECOND_BOUNDARY = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("older", "fresher", "gap"),
    [
        (
            SECOND_BOUNDARY - timedelta(milliseconds=1),
            SECOND_BOUNDARY + timedelta(milliseconds=1),
            "2 ms across a second boundary",
        ),
        (
            SECOND_BOUNDARY + timedelta(milliseconds=1),
            SECOND_BOUNDARY + timedelta(milliseconds=999),
            "998 ms inside one second",
        ),
        (
            SECOND_BOUNDARY,
            SECOND_BOUNDARY + timedelta(microseconds=1),
            "one microsecond, the finest gap a datetime expresses",
        ),
    ],
    ids=["boundary-straddle", "inside-one-second", "one-microsecond"],
)
def test_a_sub_second_gap_decides_wherever_the_second_boundary_falls(
    older: datetime, fresher: datetime, gap: str
) -> None:
    """The defect ADR 0016 removed: under truncation the middle row tied and the first did not.

    Two claims 2 ms apart resolved because a second boundary happened to fall between them, while
    two claims 500x further apart went to saṃśaya because it did not. Whether the ledger can tell
    two observations apart is now a fact about the observations.
    """
    source = _source()
    sources = {source.id: source}

    comparison = compare_priority(
        _belief("b_fresh", source, fresher), _belief("b_old", source, older), sources, CONFIG
    )

    assert comparison.decisive_field == "recency", gap
    assert comparison.result == 1
    assert (
        compare_priority(
            _belief("b_old", source, older), _belief("b_fresh", source, fresher), sources, CONFIG
        ).result
        == -1
    )


@pytest.mark.parametrize("year", [2026, 2038, 2100, 2260])
def test_distinct_instants_never_share_a_rank_at_any_epoch(year: int) -> None:
    """Guards the arithmetic, not just the granularity.

    `int(observed_at.timestamp() * 1_000_000)` is the obvious way to write this key and is wrong:
    a float64 holding seconds since 1970 spaces its representable values ~0.24 us apart today and
    ~1.9 us apart in 2260, so it collapses adjacent microseconds back into ties — measured at 0% of
    pairs in 2026, 9.7% in 2038 and 50% in 2260. A tie that arrives with a calendar date is worse
    than the one ADR 0016 removed, because no test written today would see it. The engine uses
    integer arithmetic on the timedelta, which has no such horizon; these epochs are the fixtures
    that would fail under the float route.
    """
    source = _source()
    base = datetime(year, 6, 1, 12, 0, 0, tzinfo=UTC)
    ranks = {
        priority_trace(
            _belief(f"b_{offset}", source, base + timedelta(microseconds=offset)), source, CONFIG
        ).recency_rank
        for offset in range(64)
    }

    assert len(ranks) == 64
    assert ranks == {_micros(base) + offset for offset in range(64)}


def test_recency_cannot_override_an_earlier_key() -> None:
    """Fifth means fifth: a fresher belief with lower integrity still loses."""
    untrusted = _source("src_untrusted", integrity=Integrity.UNTRUSTED)
    trusted = _source("src_trusted", integrity=Integrity.TRUSTED)
    fresher = _belief("b_fresh", untrusted, FRESHER)
    older = _belief("b_old", trusted, OLDER)
    sources = {untrusted.id: untrusted, trusted.id: trusted}

    comparison = compare_priority(fresher, older, sources, CONFIG)

    assert comparison.decisive_field == "integrity"
    assert comparison.attacker.recency_rank > comparison.target.recency_rank
    assert comparison.result == -1


def test_recency_cannot_override_reliability_or_specificity() -> None:
    weak = _source("src_weak", competence=0.2)
    strong = _source("src_strong", competence=0.9)
    fresher = _belief("b_fresh", weak, FRESHER)
    older = _belief("b_old", strong, OLDER)
    sources = {weak.id: weak, strong.id: strong}

    comparison = compare_priority(fresher, older, sources, CONFIG)

    assert comparison.decisive_field == "reliability"
    assert comparison.result == -1


def test_positive_over_anupalabdhi_still_precedes_the_whole_comparison() -> None:
    """The fixed rule is checked before the tuple, so a fresher absence still loses."""
    source = _source()
    fresh_absence = Belief(
        "b_absence",
        "ep_recency",
        "no such record exists",
        "no such record exists",
        Pramana.ANUPALABDHI,
        source.id,
        (EvidenceRef("ev_absence"),),
        (),
        {},
        Perishability.SLOW,
        FRESHER,
        Stakes.LOW,
        Status.IN,
        Status.IN,
    )
    old_positive = _belief("b_positive", source, OLDER)
    sources = {source.id: source}

    comparison = compare_priority(old_positive, fresh_absence, sources, CONFIG)

    assert comparison.decisive_field == "fixed_rule"
    assert comparison.fixed_rule == "positive_over_anupalabdhi"
    assert comparison.result == 1
    assert comparison.attacker.recency_rank < comparison.target.recency_rank


def test_identical_timestamps_still_reach_samsaya() -> None:
    """Recency resolves stale-versus-fresh. It does not abolish saṃśaya."""
    source = _source()
    sources = {source.id: source}

    comparison = compare_priority(
        _belief("b_a", source, OLDER), _belief("b_b", source, OLDER), sources, CONFIG
    )

    assert comparison.result == 0
    assert comparison.decisive_field == "equal"


def test_a_stale_versus_fresh_pair_that_was_pending_now_resolves() -> None:
    """The measurable point of the change, asserted end to end through relabel.

    Two contradictory SLOW beliefs differing only in age previously produced two PENDING beliefs
    and a `samsaya:` cause. They now produce one IN and one OUT.
    """
    source = _source()
    fresher = _belief("b_fresh", source, FRESHER)
    older = _belief("b_old", source, OLDER)
    beliefs = {fresher.id: fresher, older.id: older}
    edges = [
        DefeatEdge("d_1", "ep_recency", fresher.id, older.id, DefeatKind.REBUT, "contradiction"),
        DefeatEdge("d_2", "ep_recency", older.id, fresher.id, DefeatKind.REBUT, "contradiction"),
    ]

    supports = (_support(fresher), _support(older))

    result = relabel(beliefs, (), supports, edges, {source.id: source}, CONFIG)

    assert result.statuses[fresher.id] is Status.IN
    assert result.statuses[older.id] is Status.OUT
    pending = [
        belief_id for belief_id, status in result.statuses.items() if status is Status.PENDING
    ]
    assert pending == [], "the stale-versus-fresh pair must no longer produce saṃśaya"
    assert not any(cause.startswith("samsaya:") for cause in result.causes.values())


def test_the_same_pair_at_one_timestamp_is_still_pending() -> None:
    """The control for the test above: without an age difference, nothing changed."""
    source = _source()
    left = _belief("b_left", source, OLDER)
    right = _belief("b_right", source, OLDER)
    beliefs = {left.id: left, right.id: right}
    edges = [
        DefeatEdge("d_1", "ep_recency", left.id, right.id, DefeatKind.REBUT, "contradiction"),
        DefeatEdge("d_2", "ep_recency", right.id, left.id, DefeatKind.REBUT, "contradiction"),
    ]

    supports = (_support(left), _support(right))

    result = relabel(beliefs, (), supports, edges, {source.id: source}, CONFIG)

    assert result.statuses[left.id] is Status.PENDING
    assert result.statuses[right.id] is Status.PENDING
    assert any(cause.startswith("samsaya:") for cause in result.causes.values())


def test_a_naive_observed_at_is_refused_at_construction() -> None:
    """Making recency unconditional made the engine's naive-datetime raise reachable everywhere.

    The guarantee is enforced at the model boundary instead, so an invalid value cannot reach
    defeat resolution at all.
    """
    source = _source()

    with pytest.raises(ValueError, match="timezone-aware"):
        _belief("b_naive", source, datetime(2026, 7, 11, 9, 0))


def test_the_engines_own_timezone_guard_is_kept_as_defence_in_depth() -> None:
    """`_recency_micros` still refuses a naive value even though construction should have.

    The constructor makes this unreachable through any ordinary path, which is exactly why it is
    worth pinning: the guard is deliberate redundancy, not dead code left behind, and a future
    caller building a trace from something that is not a `Belief` still hits it. The frozen
    dataclass is mutated directly here because that is the only way to produce the state the guard
    defends against.
    """
    source = _source()
    belief = _belief("b_bypass", source, FRESHER)
    object.__setattr__(belief, "observed_at", datetime(2026, 7, 11, 9, 0))

    with pytest.raises(ValueError, match="timezone-aware"):
        priority_trace(belief, source, CONFIG)
