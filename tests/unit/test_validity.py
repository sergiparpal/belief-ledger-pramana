from __future__ import annotations

from datetime import UTC, datetime

from belief_ledger_pramana.engine.validity import validate_belief
from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.models import (
    Belief,
    EvidenceRef,
    Perishability,
    Pramana,
    Stakes,
    Status,
)


def _belief(pramana: Pramana, validity: dict, evidence: tuple[EvidenceRef, ...] = ()) -> Belief:
    return Belief(
        new_id("belief"),
        new_id("episode"),
        "Package Foo exists",
        "package foo exists",
        pramana,
        new_id("source"),
        evidence,
        (),
        {},
        Perishability.SLOW,
        datetime.now(UTC),
        Stakes.MED,
        Status.IN,
        Status.IN,
        validity=validity,
    )


def test_hash_only_cannot_support_shabda_content() -> None:
    evidence_id = new_id("evidence")
    belief = _belief(
        Pramana.SHABDA,
        {"apta": 0.7, "assertive": True},
        (EvidenceRef(evidence_id, (0, 18)),),
    )
    result = validate_belief(
        belief,
        evidence_payloads={evidence_id: None},
        evidence_mode="hash_only",
    )
    assert not result.valid
    assert "hash_only" in " ".join(result.reasons)


def test_underqualified_absence_is_invalid() -> None:
    evidence_id = new_id("evidence")
    belief = _belief(
        Pramana.ANUPALABDHI,
        {
            "search_succeeded": True,
            "truncated": False,
            "corpus": "repo",
            "scope": "src",
            "query": "legacy_mode",
            "parameters": {"case": True},
            "coverage": 0.9,
            "recall": 0.4,
        },
        (EvidenceRef(evidence_id),),
    )
    result = validate_belief(belief)
    assert not result.valid
    assert any("recall" in reason for reason in result.reasons)


def test_pratyaksha_cannot_exceed_measured_boundary() -> None:
    belief = _belief(
        Pramana.PRATYAKSHA,
        {"tool_ok": True, "parsed": True, "measured_only": False},
        (EvidenceRef(new_id("evidence")),),
    )
    result = validate_belief(belief)
    assert not result.valid
    assert any("exceeds" in reason for reason in result.reasons)
