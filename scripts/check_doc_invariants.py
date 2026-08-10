#!/usr/bin/env python3
"""Keep documented constants equal to the code they are derived from.

This is a different concern from `scripts/check_product_claims.py`. That script guards *marketing
language* against restricted claims; this one guards *derived facts* against drift. Mixing them
would make both harder to reason about, so they stay separate scripts with separate failure modes.

Each fact names one source of truth in code, one expected value extracted from it, and the
documents that must state it. A document states a fact by matching a pattern with exactly one
capture group; the captured text must equal the extracted value. A document listed for a fact that
matches the pattern nowhere fails just as loudly as one that matches with the wrong value, because
a fact silently dropped from a document is the same drift as a fact left stale in it.

Run against an alternate tree with `--root`, which is how the negative test proves the checker
actually fails.
"""

from __future__ import annotations

import argparse
import ast
import re
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]

MIGRATIONS_MODULE = "packages/core/src/belief_ledger_core/migrations.py"
HERMES_CONTRACT_SCRIPT = "scripts/check_hermes_contract.py"
SMOKE_INSTALL_SCRIPT = "scripts/smoke_install.py"
CI_WORKFLOW = ".github/workflows/ci.yml"
CORE_MIGRATION_DIR = "packages/core/src/belief_ledger_core/data/migrations"
COMPAT_MIGRATION_DIR = "belief_ledger_pramana/data/migrations"


class ExtractionError(RuntimeError):
    """The source of truth could not be read. Never a documentation failure."""


@dataclass(frozen=True)
class DocRequirement:
    """One document that must state one fact, and the shape it states it in.

    `scope` is `"all"` when every occurrence must be current, and `"first"` when only the first
    one must be. `"first"` exists for `CHANGELOG.md`, whose older release headings legitimately
    carry older versions and must keep carrying them.
    """

    path: str
    pattern: str
    scope: Literal["all", "first"] = "all"

    def compiled(self) -> re.Pattern[str]:
        compiled = re.compile(self.pattern)
        if compiled.groups != 1:
            raise ExtractionError(
                f"{self.path}: pattern {self.pattern!r} must have exactly one capture group"
            )
        return compiled


@dataclass(frozen=True)
class Fact:
    name: str
    source: str
    extract: Callable[[Path], str]
    requirements: tuple[DocRequirement, ...]


def _module_constant(relative: str, name: str) -> Callable[[Path], str]:
    """Read a module-level assignment with `ast`, never by importing or by regex."""

    def extract(root: Path) -> str:
        path = root / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except OSError as error:
            raise ExtractionError(f"{relative}: unreadable ({error})") from error
        for node in tree.body:
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if not any(isinstance(item, ast.Name) and item.id == name for item in targets):
                continue
            if node.value is None:
                continue
            try:
                return str(ast.literal_eval(node.value))
            except ValueError as error:
                raise ExtractionError(f"{relative}: {name} is not a literal ({error})") from error
        raise ExtractionError(f"{relative}: no module-level assignment to {name}")

    return extract


def _pyproject_field(*keys: str) -> Callable[[Path], str]:
    def extract(root: Path) -> str:
        path = root / "pyproject.toml"
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ExtractionError(f"pyproject.toml: unreadable ({error})") from error
        cursor: object = data
        for key in keys:
            if not isinstance(cursor, dict) or key not in cursor:
                raise ExtractionError(f"pyproject.toml: missing {'.'.join(keys)}")
            cursor = cursor[key]
        return str(cursor)

    return extract


def _ci_cryptography_bound(root: Path) -> str:
    """The bound CI actually installs, taken from the workflow rather than from prose.

    Every occurrence in the workflow must agree. An intra-file disagreement is itself the drift
    this fact exists to catch, so it is reported as an extraction failure rather than silently
    resolved to the first match.
    """
    path = root / CI_WORKFLOW
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ExtractionError(f"{CI_WORKFLOW}: unreadable ({error})") from error
    found = set(re.findall(r"cryptography(>=[\d.]+,<[\d.]+)", text))
    if not found:
        raise ExtractionError(f"{CI_WORKFLOW}: no cryptography override found")
    if len(found) > 1:
        raise ExtractionError(f"{CI_WORKFLOW}: disagreeing cryptography overrides {sorted(found)}")
    return f"cryptography{found.pop()}"


FACTS: tuple[Fact, ...] = (
    Fact(
        name="latest_schema_version",
        source=f"{MIGRATIONS_MODULE}:LATEST_SCHEMA_VERSION",
        extract=_module_constant(MIGRATIONS_MODULE, "LATEST_SCHEMA_VERSION"),
        requirements=(
            DocRequirement(
                "docs/upgrade-and-rollback.md", r"[Tt]he current schema(?: version)? is (\d+)"
            ),
            DocRequirement("docs/operations.md", r"[Tt]he current schema(?: version)? is (\d+)"),
            DocRequirement("docs/architecture.md", r"[Tt]he current schema(?: version)? is (\d+)"),
        ),
    ),
    Fact(
        name="package_version",
        source="pyproject.toml:project.version",
        extract=_pyproject_field("project", "version"),
        requirements=(
            DocRequirement("README.md", r"synchronized `([^`]+)` local distributions"),
            DocRequirement("RELEASE_NOTES.md", r"distributions advance to `([^`]+)`"),
            DocRequirement(
                "CHANGELOG.md",
                r"(?m)^## v[0-9][^ ]* / (\S+) - \d{4}-\d{2}-\d{2}$",
                scope="first",
            ),
        ),
    ),
    Fact(
        name="requires_python",
        source="pyproject.toml:project.requires-python",
        extract=_pyproject_field("project", "requires-python"),
        requirements=(
            DocRequirement("README.md", r"[Ss]upported Python range is `([^`]+)`"),
            DocRequirement("HERMES_COMPATIBILITY.md", r"with Python\n`([^`]+)`"),
            DocRequirement("docs/integrations/hermes.md", r"Python `([^`]+)`"),
        ),
    ),
    Fact(
        name="hermes_audited_version",
        source=f"{HERMES_CONTRACT_SCRIPT}:AUDITED_VERSION",
        extract=_module_constant(HERMES_CONTRACT_SCRIPT, "AUDITED_VERSION"),
        requirements=(
            DocRequirement("README.md", r"audited Hermes Agent `([^`]+)` contract"),
            DocRequirement(
                "HERMES_COMPATIBILITY.md", r"pinned to Hermes Agent `([^`]+)`, audited at commit"
            ),
            DocRequirement("docs/integrations/hermes.md", r"contract is Hermes Agent `([^`]+)`"),
            DocRequirement(SMOKE_INSTALL_SCRIPT, r'"hermes-agent==([^"]+)"'),
        ),
    ),
    Fact(
        name="hermes_audited_commit",
        source=f"{HERMES_CONTRACT_SCRIPT}:AUDITED_COMMIT",
        extract=_module_constant(HERMES_CONTRACT_SCRIPT, "AUDITED_COMMIT"),
        requirements=(
            DocRequirement("README.md", r"contract at commit\n`([0-9a-f]{40})`"),
            DocRequirement("HERMES_COMPATIBILITY.md", r"audited at commit\n`([0-9a-f]{40})`"),
            DocRequirement("docs/integrations/hermes.md", r"at commit\n`([0-9a-f]{40})`"),
        ),
    ),
    Fact(
        name="cryptography_override",
        source=f"{CI_WORKFLOW}:cryptography override",
        extract=_ci_cryptography_bound,
        requirements=(
            DocRequirement("HERMES_COMPATIBILITY.md", r"and `(cryptography>=[\d.]+,<[\d.]+)`"),
            DocRequirement("docs/integrations/hermes.md", r"and `(cryptography>=[\d.]+,<[\d.]+)`"),
            DocRequirement(SMOKE_INSTALL_SCRIPT, r'"(cryptography>=[\d.]+,<[\d.]+)"'),
        ),
    ),
)


def _documented_values(root: Path, requirement: DocRequirement) -> list[tuple[int, str]]:
    """Every match of the requirement's pattern, as (line number, captured value).

    Matching runs over the whole text rather than line by line, because several of these facts are
    stated across a line wrap in the documents that carry them.
    """
    path = root / requirement.path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    matches: list[tuple[int, str]] = []
    for match in requirement.compiled().finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        matches.append((line, match.group(1)))
        if requirement.scope == "first":
            break
    return matches


def fact_failures(root: Path, facts: Iterable[Fact] = FACTS) -> list[str]:
    failures: list[str] = []
    for fact in facts:
        try:
            expected = fact.extract(root)
        except ExtractionError as error:
            failures.append(f"{fact.name}: cannot read source of truth: {error}")
            continue
        for requirement in fact.requirements:
            if not (root / requirement.path).is_file():
                failures.append(
                    f"{fact.name}: expected {expected!r} from {fact.source}; "
                    f"{requirement.path} does not exist"
                )
                continue
            matches = _documented_values(root, requirement)
            if not matches:
                failures.append(
                    f"{fact.name}: expected {expected!r} from {fact.source}; "
                    f"{requirement.path} states it nowhere "
                    f"(no match for {requirement.pattern!r})"
                )
                continue
            for line, found in matches:
                if found != expected:
                    failures.append(
                        f"{fact.name}: {requirement.path}:{line}: "
                        f"expected {expected!r} from {fact.source}, found {found!r}"
                    )
    return failures


def migration_coverage_failures(root: Path) -> list[str]:
    """Every schema version in 1..LATEST must have a SQL file or an in-code constant.

    Versions 4, 5 and 7 deliberately have no DDL file; they exist only as `SCHEMA_V*` constants.
    Without this check a genuinely missing version looks exactly like that deliberate split.
    """
    try:
        latest = int(_module_constant(MIGRATIONS_MODULE, "LATEST_SCHEMA_VERSION")(root))
    except (ExtractionError, ValueError) as error:
        return [f"migration_coverage: cannot read LATEST_SCHEMA_VERSION: {error}"]

    try:
        tree = ast.parse((root / MIGRATIONS_MODULE).read_text(encoding="utf-8"))
    except OSError as error:
        return [f"migration_coverage: {MIGRATIONS_MODULE} unreadable ({error})"]
    constants = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and re.fullmatch(r"SCHEMA_V\d+", target.id)
    }

    failures: list[str] = []
    for directory in (CORE_MIGRATION_DIR, COMPAT_MIGRATION_DIR):
        sql_versions = {
            int(match.group(1))
            for path in sorted((root / directory).glob("*.sql"))
            if (match := re.match(r"(\d+)_", path.name))
        }
        for version in range(1, latest + 1):
            if version in sql_versions or f"SCHEMA_V{version}" in constants:
                continue
            failures.append(
                f"migration_coverage: schema version {version} of {latest} has neither "
                f"{directory}/{version:04d}_*.sql nor a SCHEMA_V{version} constant in "
                f"{MIGRATIONS_MODULE}"
            )
        for version in sorted(sql_versions):
            if version > latest:
                failures.append(
                    f"migration_coverage: {directory} carries version {version} above "
                    f"LATEST_SCHEMA_VERSION {latest}"
                )
    return failures


def check(root: Path) -> list[str]:
    return fact_failures(root) + migration_coverage_failures(root)


def required_paths() -> tuple[str, ...]:
    """Every path the checker reads, so a test can build a minimal alternate root."""
    paths = {
        MIGRATIONS_MODULE,
        HERMES_CONTRACT_SCRIPT,
        SMOKE_INSTALL_SCRIPT,
        CI_WORKFLOW,
        "pyproject.toml",
    }
    for fact in FACTS:
        paths.update(requirement.path for requirement in fact.requirements)
    return tuple(sorted(paths))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="tree to check; defaults to the repository this script lives in",
    )
    args = parser.parse_args()
    failures = check(args.root)
    if failures:
        print("\n".join(failures))
        return 1
    documents = {requirement.path for fact in FACTS for requirement in fact.requirements}
    print(
        f"documented constants match code: {len(FACTS)} facts across {len(documents)} files, "
        "and every schema version has a migration"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
