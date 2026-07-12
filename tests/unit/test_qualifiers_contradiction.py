from __future__ import annotations

from datetime import UTC, datetime

from belief_ledger_pramana.engine.contradiction import classify_deterministically
from belief_ledger_pramana.engine.qualifiers import canonicalize_qualifiers, reconcile_qualifiers
from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.models import (
    Belief,
    Perishability,
    Pramana,
    Stakes,
    Status,
)


def _belief(content: str, qualifiers: dict[str, str]) -> Belief:
    return Belief(
        id=new_id("belief"),
        episode_id=new_id("episode"),
        content=content,
        normalized_content=content.casefold(),
        pramana=Pramana.SHABDA,
        source_id=new_id("source"),
        evidence=(),
        justifications=(),
        qualifiers=qualifiers,
        perishability=Perishability.FAST,
        observed_at=datetime.now(UTC),
        stakes=Stakes.MED,
        status=Status.IN,
        admission_status=Status.IN,
    )


def test_time_scoped_claims_coexist() -> None:
    left = _belief("Foo version is 2.0", {"as_of": "2024-01-01"})
    right = _belief("Foo version is 3.0", {"as_of": "2026-01-01"})
    decision = classify_deterministically(left, right)
    assert decision.outcome == "scope_mismatch"


def test_same_scope_numeric_values_rebut() -> None:
    left = _belief("Foo version is 2.0", {"as_of": "2026-01-01"})
    right = _belief("Foo version is 3.0", {"as_of": "2026-01-01"})
    assert classify_deterministically(left, right).outcome == "rebut"


def test_qualifier_aliases_are_canonical() -> None:
    assert canonicalize_qualifiers({"assumes": "  Linux  ", "unit": "sec"}) == {
        "assumptions": "Linux",
        "units": "sec",
    }
    assert not reconcile_qualifiers({"scope": "local"}, {"scope": "remote"}).compatible


def test_invalid_temporal_qualifiers_never_compare_lexically() -> None:
    scope = reconcile_qualifiers({"valid_from": "tomorrow-ish"}, {"valid_to": "2026-01-01"})
    assert not scope.compatible
    assert scope.reason == "invalid temporal qualifier"
