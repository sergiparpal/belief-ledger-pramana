"""Architecture guardrails for the dependency direction introduced by the refactor."""

from __future__ import annotations

from pathlib import Path

from belief_ledger_pramana.config import ConfigSnapshot, packaged_yaml
from belief_ledger_pramana.events import EventDraft
from belief_ledger_pramana.store import EventDraft as LegacyEventDraft


def test_typed_settings_capture_the_validated_application_configuration() -> None:
    data = packaged_yaml("defaults.yaml")
    snapshot = ConfigSnapshot(data, None, (), "test", None)

    assert snapshot.settings.gating.enabled
    assert snapshot.settings.verification.max_llm_calls_per_turn == 3
    assert snapshot.settings.ingestion.near_duplicate_threshold == 0.92


def test_event_draft_is_storage_neutral_with_a_legacy_store_reexport() -> None:
    assert EventDraft is LegacyEventDraft


def test_dependency_layers_do_not_bypass_their_declared_boundaries() -> None:
    package = Path(__file__).parents[2] / "belief_ledger_pramana"
    engine_and_domain = [package / "engine", package / "ingestion", package / "context"]

    for directory in engine_and_domain:
        for source in directory.rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            assert "..store import" not in text
            assert "..hermes" not in text

    for source in (package / "application").rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "sqlite3" not in text
        assert "..store import" not in text
        assert "..hermes" not in text

    for source in (package / "hermes").rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "EventDraft" not in text
        assert ".append_events(" not in text
