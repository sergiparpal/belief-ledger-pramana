"""Auditable source-stat counter transitions."""

from __future__ import annotations

from ..models import Source, SourceStats


def updated_source(source: Source, *, confirmed: int = 0, defeated: int = 0) -> Source:
    if confirmed < 0 or defeated < 0:
        raise ValueError("source-stat increments cannot be negative")
    stats = SourceStats(
        confirmed=source.stats.confirmed + confirmed,
        defeated=source.stats.defeated + defeated,
        samples=source.stats.samples + confirmed + defeated,
    )
    return Source(
        id=source.id,
        episode_id=source.episode_id,
        kind=source.kind,
        integrity=source.integrity,
        name=source.name,
        root=source.root,
        competence=dict(source.competence),
        stats=stats,
    )
