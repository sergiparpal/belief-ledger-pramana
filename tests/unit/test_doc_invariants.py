from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from belief_ledger_core.migrations import LATEST_SCHEMA_VERSION

from scripts.check_doc_invariants import (
    FACTS,
    ExtractionError,
    check,
    fact_failures,
    migration_coverage_failures,
    required_paths,
)

REPOSITORY = Path(__file__).resolve().parents[2]


def _mirror(destination: Path) -> Path:
    """A minimal copy of the repository holding exactly what the checker reads."""
    for relative in required_paths():
        source = REPOSITORY / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for directory in (
        "packages/core/src/belief_ledger_core/data/migrations",
        "belief_ledger_pramana/data/migrations",
    ):
        shutil.copytree(REPOSITORY / directory, destination / directory)
    return destination


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPOSITORY / "scripts/check_doc_invariants.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_repository_documents_agree_with_code() -> None:
    assert check(REPOSITORY) == []


def test_checker_exits_zero_on_the_real_tree() -> None:
    assert _run(REPOSITORY).returncode == 0


def test_every_requirement_pattern_has_exactly_one_capture_group() -> None:
    for fact in FACTS:
        for requirement in fact.requirements:
            assert requirement.compiled().groups == 1, f"{fact.name} -> {requirement.path}"


def test_a_mutated_constant_is_caught_and_the_file_is_named(tmp_path: Path) -> None:
    """The load-bearing negative test: a guard never observed failing is not a guard."""
    root = _mirror(tmp_path / "tree")
    current = LATEST_SCHEMA_VERSION
    stale = current - 1
    document = root / "docs/upgrade-and-rollback.md"
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            f"The current schema is {current}", f"The current schema is {stale}"
        ),
        encoding="utf-8",
    )

    result = _run(root)

    assert result.returncode != 0
    assert "docs/upgrade-and-rollback.md" in result.stdout
    assert "latest_schema_version" in result.stdout
    assert f"expected '{current}'" in result.stdout
    assert f"found '{stale}'" in result.stdout


def test_a_document_that_drops_the_fact_entirely_fails(tmp_path: Path) -> None:
    """Deleting the statement must fail as loudly as leaving it stale."""
    root = _mirror(tmp_path / "tree")
    document = root / "README.md"
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            "The supported Python range is `>=3.11,<3.14`, and CI runs the matrix\nacross every"
            " version in it.",
            "",
        ),
        encoding="utf-8",
    )

    result = _run(root)

    assert result.returncode != 0
    assert "README.md states it nowhere" in result.stdout
    assert "requires_python" in result.stdout


def test_a_drifted_package_version_is_caught_in_release_notes(tmp_path: Path) -> None:
    root = _mirror(tmp_path / "tree")
    notes = root / "RELEASE_NOTES.md"
    notes.write_text(
        notes.read_text(encoding="utf-8").replace(
            "distributions advance to `1.0.0rc4`", "distributions advance to `1.0.0rc3`"
        ),
        encoding="utf-8",
    )

    failures = fact_failures(root)

    assert any("package_version" in item and "RELEASE_NOTES.md" in item for item in failures)
    assert any("found '1.0.0rc3'" in item for item in failures)


def test_older_changelog_headings_may_keep_older_versions(tmp_path: Path) -> None:
    """`scope="first"` exists so release history stays historical rather than rewritten."""
    root = _mirror(tmp_path / "tree")
    changelog = root / "CHANGELOG.md"
    assert "## v0.2.0 / 1.0.0rc3 - 2026-08-01" in changelog.read_text(encoding="utf-8")

    assert [item for item in fact_failures(root) if "CHANGELOG.md" in item] == []


def test_a_disagreeing_cryptography_override_is_an_extraction_failure(tmp_path: Path) -> None:
    """Intra-workflow drift is the very drift this fact exists to catch."""
    root = _mirror(tmp_path / "tree")
    workflow = root / ".github/workflows/ci.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        text.replace("cryptography>=50.0.0,<51", "cryptography>=49.0.0,<50", 1), encoding="utf-8"
    )

    failures = fact_failures(root)

    assert any("cryptography_override" in item and "disagreeing" in item for item in failures), (
        failures
    )


def test_a_hermes_commit_edit_in_one_document_is_caught(tmp_path: Path) -> None:
    root = _mirror(tmp_path / "tree")
    document = root / "docs/integrations/hermes.md"
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            "3ef6bbd201263d354fd83ec55b3c306ded2eb72a", "0" * 40
        ),
        encoding="utf-8",
    )

    failures = fact_failures(root)

    assert any(
        "hermes_audited_commit" in item and "docs/integrations/hermes.md" in item
        for item in failures
    ), failures


def test_a_schema_version_without_a_migration_is_reported(tmp_path: Path) -> None:
    root = _mirror(tmp_path / "tree")
    module = root / "packages/core/src/belief_ledger_core/migrations.py"
    unreachable = LATEST_SCHEMA_VERSION + 1
    module.write_text(
        module.read_text(encoding="utf-8").replace(
            f"LATEST_SCHEMA_VERSION = {LATEST_SCHEMA_VERSION}",
            f"LATEST_SCHEMA_VERSION = {unreachable}",
        ),
        encoding="utf-8",
    )

    failures = migration_coverage_failures(root)

    assert any(f"schema version {unreachable} of {unreachable}" in item for item in failures), (
        failures
    )


def test_a_sql_file_ahead_of_the_latest_version_is_reported(tmp_path: Path) -> None:
    root = _mirror(tmp_path / "tree")
    ahead = LATEST_SCHEMA_VERSION + 1
    (
        root / f"packages/core/src/belief_ledger_core/data/migrations/{ahead:04d}_future.sql"
    ).write_text("-- not reachable from LATEST_SCHEMA_VERSION\n", encoding="utf-8")

    failures = migration_coverage_failures(root)

    assert any(f"carries version {ahead} above" in item for item in failures), failures


def test_current_migration_coverage_is_complete() -> None:
    assert migration_coverage_failures(REPOSITORY) == []


def test_an_unreadable_source_of_truth_is_not_reported_as_documentation_drift(
    tmp_path: Path,
) -> None:
    root = _mirror(tmp_path / "tree")
    (root / "packages/core/src/belief_ledger_core/migrations.py").write_text(
        "LATEST_SCHEMA_VERSION = int(open('x').read())\n", encoding="utf-8"
    )

    failures = fact_failures(root)

    assert any("cannot read source of truth" in item for item in failures), failures
    assert not any("states it nowhere" in item for item in failures)


def test_a_pattern_without_a_capture_group_is_rejected() -> None:
    from scripts.check_doc_invariants import DocRequirement

    with pytest.raises(ExtractionError):
        DocRequirement("README.md", r"no group here").compiled()
