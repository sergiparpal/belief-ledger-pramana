from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from belief_ledger_pramana.config import load_config
from belief_ledger_pramana.engine.trust import determine_admission
from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.models import (
    Belief,
    Integrity,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    Status,
)


@pytest.mark.parametrize("stakes", list(Stakes))
@pytest.mark.parametrize(
    ("profile", "pramana", "kind", "integrity", "about_self"),
    [
        ("pratyaksha_tool", Pramana.PRATYAKSHA, SourceKind.TOOL, Integrity.TRUSTED, False),
        (
            "shabda_internal_trusted",
            Pramana.SHABDA,
            SourceKind.DOCUMENT,
            Integrity.TRUSTED,
            False,
        ),
        ("shabda_web_semi", Pramana.SHABDA, SourceKind.WEB, Integrity.SEMI, False),
        (
            "shabda_web_untrusted",
            Pramana.SHABDA,
            SourceKind.WEB,
            Integrity.UNTRUSTED,
            False,
        ),
        ("user_self", Pramana.SHABDA, SourceKind.USER, Integrity.SEMI, True),
        ("user_world", Pramana.SHABDA, SourceKind.USER, Integrity.SEMI, False),
        ("anumana_registered", Pramana.ANUMANA, SourceKind.MODEL, Integrity.SEMI, False),
        (
            "anupalabdhi",
            Pramana.ANUPALABDHI,
            SourceKind.TOOL,
            Integrity.TRUSTED,
            False,
        ),
    ],
)
def test_every_trust_matrix_cell(
    tmp_path: Path,
    stakes: Stakes,
    profile: str,
    pramana: Pramana,
    kind: SourceKind,
    integrity: Integrity,
    about_self: bool,
) -> None:
    config, _ = load_config(hermes_home=tmp_path)
    episode_id = new_id("episode")
    source = Source(
        new_id("source"),
        episode_id,
        kind,
        integrity,
        "source",
        "root",
        {"general": 0.5},
        SourceStats(),
    )
    belief = Belief(
        id=new_id("belief"),
        episode_id=episode_id,
        content="Atomic proposition holds",
        normalized_content="atomic proposition holds",
        pramana=pramana,
        source_id=source.id,
        evidence=(),
        justifications=(),
        qualifiers={},
        perishability=Perishability.SLOW,
        observed_at=datetime.now(UTC),
        stakes=stakes,
        status=Status.PENDING,
        admission_status=Status.PENDING,
        validity={"about_self": about_self},
    )
    decision = determine_admission(
        belief,
        source,
        config.data,
        episode_stakes=stakes,
    )
    cell = config.data["trust"]["matrix"][profile][stakes.value]
    expected = {
        "svatah": Status.IN,
        "yogyata": Status.IN,
        "paratah": Status.PENDING,
        "quarantine": Status.QUARANTINED,
        "reject": Status.OUT,
    }[cell["mode"]]
    assert decision.profile == profile
    assert decision.status is expected
