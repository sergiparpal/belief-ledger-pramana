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


def test_packaged_policy_data_has_exactly_one_copy() -> None:
    """Trust and policy data lives in core alone, and the adapter reads it from there.

    This used to be a byte-identity assertion across two copies, which is a workaround for
    duplication rather than a fix: it detects drift but cannot prevent it, and it only holds while
    someone remembers to keep both files in step. The adapter depends on core, so core's copy is
    always installed alongside it and a second copy could only ever diverge (ADR 0015).
    """

    root = Path(__file__).parents[2]
    adapter = root / "belief_ledger_pramana" / "data"
    core = root / "packages" / "core" / "src" / "belief_ledger_core" / "data"
    names = ("action-policies.yaml", "defaults.yaml", "source-profiles.yaml")

    for name in names:
        assert (core / name).is_file(), name
        assert not (adapter / name).exists(), (
            f"{name} reappeared in the adapter; packaged policy data has one home"
        )

    assert list(adapter.glob("*.yaml")) == []


def test_the_adapter_resolves_packaged_data_from_core() -> None:
    """The single copy is reached through the dependency, not through a path guess."""
    from belief_ledger_pramana import config as adapter_config

    assert adapter_config.data_package.__name__ == "belief_ledger_core.data"
    assert adapter_config.packaged_yaml("defaults.yaml")["verification"]["max_llm_calls_per_turn"]


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


# Q4 of the obvious-fix plan selected a 600-line limit. Stage 7d split `runtime.py` (3,233 lines)
# into a package by pure moves; the files below are what remained above the limit afterwards, each
# with the reason it could not be moved and the size it must not exceed.
#
# The ceilings are the point. A bare exemption list rots into permission to grow; a ceiling means
# an exempt file can be reduced but never enlarged, so the list can only move in one direction.
SOURCE_LINE_LIMIT = 600
OVERSIZED_EXEMPTIONS: dict[str, tuple[int, str]] = {
    "belief_ledger_pramana/runtime/episode_service.py": (
        2430,
        "One class, EpisodeService. Splitting it across modules is not a pure move — it needs "
        "mixins or method relocation, both of which change the class rather than move it — so "
        "Stage 7d's hard rule left it in place. See F-23.",
    ),
    "packages/core/src/belief_ledger_core/store.py": (
        1601,
        "One class, LedgerStore, holding the connection lifecycle that every method shares. "
        "Same blocker as episode_service.py.",
    ),
    "packages/core/src/belief_ledger_core/api.py": (
        1178,
        "One class, BeliefLedger: the public core API. Splitting it would move public methods "
        "between modules, which is a surface change wearing a refactor's clothes.",
    ),
    "packages/core/src/belief_ledger_core/enforcement.py": (
        1097,
        "Authorization chain and projection rebuild. Its functions share transaction scope and "
        "SQL that only makes sense read together.",
    ),
    "belief_ledger_pramana/config.py": (
        900,
        "Configuration dataclasses and one validator. The validator is a single long function by "
        "design: every rule is visible in one place, which is what makes it auditable.",
    ),
    "packages/core/src/belief_ledger_core/migrations.py": (
        778,
        "Mostly SQL held in module constants. Line count here is DDL, not logic.",
    ),
    "packages/core/src/belief_ledger_core/projections.py": (
        723,
        "One handler per event kind plus the dispatch table. Splitting it would separate handlers "
        "from the table that must list all of them.",
    ),
    "belief_ledger_pramana/hermes/cli.py": (
        879,
        "One argparse tree and one dispatch function. Grew past the limit in Stages 4 to 6, which "
        "added the divergence, anchor and snapshot commands. Ceiling moved twice: 723 to 738 in "
        "Stage 8 for the `replay_budget` check in `doctor`, and 738 to 879 for the doctor "
        "severity split — the `notices` list, the `anchor` check with its own comparison helper, "
        "and the `strict_guarantee` check. The guard caught both additions, which is the intended "
        "workflow — a ceiling moves only in a change that says why. The anchor helper is the "
        "natural seam if this file is split: it is self-contained and has no argparse coupling.",
    ),
}


def _source_files() -> dict[str, int]:
    root = Path(__file__).parents[2]
    roots = [root / "belief_ledger_pramana", *(root / "packages").glob("*/src")]
    sizes: dict[str, int] = {}
    for base in roots:
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            sizes[relative] = len(path.read_text(encoding="utf-8").splitlines())
    return sizes


def test_no_new_source_file_exceeds_the_line_limit() -> None:
    """A new file over the limit fails; growing an exempt one fails too."""
    offenders = [
        f"{name} is {count} lines, over the {SOURCE_LINE_LIMIT}-line limit"
        for name, count in sorted(_source_files().items())
        if count > SOURCE_LINE_LIMIT and name not in OVERSIZED_EXEMPTIONS
    ]

    assert offenders == [], "\n".join(offenders)


def test_no_exempt_file_grows_beyond_its_recorded_ceiling() -> None:
    sizes = _source_files()
    grown = [
        f"{name} is {sizes[name]} lines, above its recorded ceiling of {ceiling}"
        for name, (ceiling, _) in sorted(OVERSIZED_EXEMPTIONS.items())
        if name in sizes and sizes[name] > ceiling
    ]

    assert grown == [], (
        "\n".join(grown)
        + "\n\nAn exemption is a ceiling, not a licence. Reduce the file, or split it."
    )


def test_the_exemption_list_holds_no_file_that_no_longer_needs_it() -> None:
    """The list can only shrink: a file that came under the limit must leave it."""
    sizes = _source_files()
    stale = [
        f"{name} no longer exists"
        if name not in sizes
        else f"{name} is {sizes[name]} lines, at or under the limit"
        for name in sorted(OVERSIZED_EXEMPTIONS)
        if name not in sizes or sizes[name] <= SOURCE_LINE_LIMIT
    ]

    assert stale == [], "\n".join(stale) + "\n\nRemove it from OVERSIZED_EXEMPTIONS."


def test_every_exemption_states_a_reason() -> None:
    for name, (ceiling, reason) in OVERSIZED_EXEMPTIONS.items():
        assert ceiling > SOURCE_LINE_LIMIT, name
        assert len(reason) > 40, f"{name}: an exemption without a real reason is a licence"
