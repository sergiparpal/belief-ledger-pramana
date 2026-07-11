from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from belief_ledger_pramana.config import load_config
from belief_ledger_pramana.engine.defeat import relabel
from belief_ledger_pramana.engine.graph import cycle_path
from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.models import (
    Belief,
    DefeatEdge,
    DefeatKind,
    EvidenceRef,
    IngestionSupport,
    Integrity,
    Justification,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    Status,
)


def _source(episode_id: str, integrity: Integrity, kind: SourceKind, competence: float) -> Source:
    return Source(
        new_id("source"),
        episode_id,
        kind,
        integrity,
        kind.value,
        f"{kind.value}:{new_id('source')}",
        {"general": competence},
        SourceStats(),
    )


def _basic(episode_id: str, source: Source, content: str, pramana: Pramana) -> Belief:
    return Belief(
        id=new_id("belief"),
        episode_id=episode_id,
        content=content,
        normalized_content=content.casefold(),
        pramana=pramana,
        source_id=source.id,
        evidence=(EvidenceRef(new_id("evidence")),),
        justifications=(),
        qualifiers={"as_of": "2026-07-11"},
        perishability=Perishability.FAST,
        observed_at=datetime.now(UTC),
        stakes=Stakes.MED,
        status=Status.IN,
        admission_status=Status.IN,
    )


def _support(belief: Belief, active: bool = True) -> IngestionSupport:
    return IngestionSupport(
        new_id("support"),
        belief.episode_id,
        belief.id,
        belief.evidence[0].evidence_id,
        {},
        active,
    )


def test_winner_propagation_and_same_run_reinstatement(tmp_path: Path) -> None:
    config, _ = load_config(hermes_home=tmp_path)
    episode_id = new_id("episode")
    observed = _source(episode_id, Integrity.TRUSTED, SourceKind.TOOL, 0.95)
    blog = _source(episode_id, Integrity.SEMI, SourceKind.WEB, 0.6)
    winner = _basic(episode_id, observed, "Foo version is 2.4.1", Pramana.PRATYAKSHA)
    loser = _basic(episode_id, blog, "Foo version is 2.3", Pramana.SHABDA)
    derived_id = new_id("belief")
    justification = Justification(
        new_id("justification"), derived_id, (loser.id,), "Use the reported latest version"
    )
    derived = Belief(
        id=derived_id,
        episode_id=episode_id,
        content="Requirements should pin Foo 2.3",
        normalized_content="requirements should pin foo 2.3",
        pramana=Pramana.ANUMANA,
        source_id=blog.id,
        evidence=(),
        justifications=(justification,),
        qualifiers={},
        perishability=Perishability.FAST,
        observed_at=datetime.now(UTC),
        stakes=Stakes.MED,
        status=Status.IN,
        admission_status=Status.IN,
    )
    edges = (
        DefeatEdge(
            new_id("defeat"), episode_id, winner.id, loser.id, DefeatKind.REBUT, "new observation"
        ),
        DefeatEdge(
            new_id("defeat"), episode_id, loser.id, winner.id, DefeatKind.REBUT, "contradiction"
        ),
    )
    supports = (_support(winner), _support(loser))
    beliefs = {item.id: item for item in (winner, loser, derived)}
    sources = {item.id: item for item in (observed, blog)}
    first = relabel(beliefs, (justification,), supports, edges, sources, config.data)
    assert first.statuses[winner.id] is Status.IN
    assert first.statuses[loser.id] is Status.OUT
    assert first.statuses[derived.id] is Status.OUT

    prior = {key: replace(value, status=first.statuses[key]) for key, value in beliefs.items()}
    second = relabel(
        prior,
        (justification,),
        (replace(supports[0], active=False), supports[1]),
        edges,
        sources,
        config.data,
    )
    assert second.statuses[winner.id] is Status.OUT
    assert second.statuses[loser.id] is Status.IN
    assert second.statuses[derived.id] is Status.IN


def test_equal_priority_is_samsaya_not_arbitrary_winner(tmp_path: Path) -> None:
    config, _ = load_config(hermes_home=tmp_path)
    episode_id = new_id("episode")
    source = _source(episode_id, Integrity.SEMI, SourceKind.USER, 0.7)
    observed_at = datetime.now(UTC)
    left = replace(
        _basic(episode_id, source, "Foo version is 2", Pramana.SHABDA),
        perishability=Perishability.SLOW,
        observed_at=observed_at,
    )
    right = replace(
        _basic(episode_id, source, "Foo version is 3", Pramana.SHABDA),
        perishability=Perishability.SLOW,
        observed_at=observed_at,
    )
    edges = (
        DefeatEdge(new_id("defeat"), episode_id, left.id, right.id, DefeatKind.REBUT, "x"),
        DefeatEdge(new_id("defeat"), episode_id, right.id, left.id, DefeatKind.REBUT, "x"),
    )
    result = relabel(
        {left.id: left, right.id: right},
        (),
        (_support(left), _support(right)),
        edges,
        {source.id: source},
        config.data,
    )
    assert result.statuses == {left.id: Status.PENDING, right.id: Status.PENDING}
    assert result.conflicts == (tuple(sorted((left.id, right.id))),)


def test_cycle_path_reports_exact_closure() -> None:
    a, b, c = (new_id("belief") for _ in range(3))
    j1 = Justification(new_id("justification"), b, (a,), "a therefore b")
    j2 = Justification(new_id("justification"), c, (b,), "b therefore c")
    assert cycle_path((j1, j2), a, (c,)) == (a, b, c, a)


def test_generated_finite_chains_terminate(tmp_path: Path) -> None:
    config, _ = load_config(hermes_home=tmp_path)
    for size in range(1, 40):
        episode_id = new_id("episode")
        source = _source(episode_id, Integrity.TRUSTED, SourceKind.TOOL, 0.9)
        root = _basic(episode_id, source, "Root is established", Pramana.PRATYAKSHA)
        beliefs = {root.id: root}
        justifications = []
        previous = root.id
        for index in range(size):
            belief_id = new_id("belief")
            justification = Justification(
                new_id("justification"), belief_id, (previous,), f"step {index}"
            )
            belief = Belief(
                belief_id,
                episode_id,
                f"Derived fact {index} holds",
                f"derived fact {index} holds",
                Pramana.ANUMANA,
                source.id,
                (),
                (justification,),
                {},
                Perishability.STABLE,
                datetime.now(UTC),
                Stakes.LOW,
                Status.IN,
                Status.IN,
            )
            beliefs[belief_id] = belief
            justifications.append(justification)
            previous = belief_id
        result = relabel(
            beliefs,
            justifications,
            (_support(root),),
            (),
            {source.id: source},
            config.data,
        )
        assert result.iterations <= size + 2
        assert all(status is Status.IN for status in result.statuses.values())
