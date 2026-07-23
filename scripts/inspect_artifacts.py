#!/usr/bin/env python3
"""Inspect wheel/sdist contents and emit a machine-readable package manifest."""

from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from pathlib import Path


def _members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return sorted(archive.namelist())
    with tarfile.open(path, mode="r:*") as archive:
        return sorted(member.name for member in archive.getmembers() if member.isfile())


def _contains(members: list[str], suffix: str) -> bool:
    return any(member.endswith(suffix) for member in members)


def _metadata(path: Path) -> str:
    if path.suffix != ".whl":
        return ""
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        return archive.read(member).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="*", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = []
    failures: list[str] = []
    forbidden = ("__pycache__/", ".pyc", ".sqlite3", ".coverage", ".venv/")
    manifest_entries: dict[Path, dict[str, str]] = {}
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        for item in manifest["artifacts"]:
            manifest_entries[Path(item["path"]).resolve()] = item
    paths = [path.resolve() for path in args.artifacts] or list(manifest_entries)
    if not paths:
        parser.error("provide artifacts or --manifest")
    for path in paths:
        members = _members(path)
        kind = "wheel" if path.suffix == ".whl" else "sdist"
        entry = manifest_entries.get(path, {})
        distribution = entry.get("distribution", "")
        if not distribution:
            distribution = "belief-ledger-core" if "core" in path.name else "belief-ledger-pramana"
        if distribution == "belief-ledger-core":
            required = [
                "belief_ledger_core/__init__.py",
                "belief_ledger_core/contracts.py",
                "belief_ledger_core/runtime.py",
                "belief_ledger_core/models.py",
                "belief_ledger_core/events.py",
                "belief_ledger_core/store.py",
                "belief_ledger_core/migrations.py",
                "belief_ledger_core/projections.py",
                "belief_ledger_core/engine/defeat.py",
                "belief_ledger_core/ingestion/adapters.py",
                "belief_ledger_core/gate/decision.py",
                "belief_ledger_core/lint/report.py",
                "belief_ledger_core/verification/scheduler.py",
                "belief_ledger_core/context/render.py",
                "belief_ledger_core/llm/client.py",
                "belief_ledger_core/data/defaults.yaml",
                "belief_ledger_core/data/source-profiles.yaml",
                "belief_ledger_core/data/action-policies.yaml",
                "belief_ledger_core/data/migrations/0003_performance_indexes.sql",
                "belief_ledger_core/data/migrations/0006_enforcement.sql",
            ]
        elif distribution == "belief-ledger-reference":
            required = [
                "belief_ledger_reference/__init__.py",
                "belief_ledger_reference/runner.py",
            ]
        else:
            required = [
                "belief_ledger_pramana/plugin.py",
                "belief_ledger_pramana/data/defaults.yaml",
                "belief_ledger_pramana/data/action-policies.yaml",
                "belief_ledger_pramana/data/source-profiles.yaml",
                "belief_ledger_pramana/data/migrations/0001_initial.sql",
                "belief_ledger_pramana/data/migrations/0002_llm_reservations.sql",
                "belief_ledger_pramana/data/migrations/0003_performance_indexes.sql",
                "belief_ledger_pramana/data/migrations/0006_enforcement.sql",
            ]
        if kind == "wheel":
            required.append(".dist-info/METADATA")
            if distribution != "belief-ledger-core":
                required.append(".dist-info/entry_points.txt")
        else:
            required.extend(("plugin.yaml", "README.md", "docs/operations.md", "tests/conftest.py"))
        missing = [item for item in required if not _contains(members, item)]
        unsafe = [member for member in members if any(token in member for token in forbidden)]
        metadata = _metadata(path)
        if "file://" in metadata or " @ " in metadata:
            failures.append(f"{path.name}: local workspace source leaked into metadata")
        if distribution == "belief-ledger-pramana" and (
            "Requires-Dist: belief-ledger-core==1.0.0rc2" not in metadata
        ):
            failures.append(f"{path.name}: missing frozen core dependency")
        if distribution == "belief-ledger-reference" and (
            "Requires-Dist: belief-ledger-core==1.0.0rc2" not in metadata
        ):
            failures.append(f"{path.name}: missing frozen core dependency")
        if distribution == "belief-ledger-core" and any(
            line.casefold().startswith("requires-dist: hermes") for line in metadata.splitlines()
        ):
            failures.append(f"{path.name}: core metadata depends on Hermes")
        if missing:
            failures.append(f"{path.name}: missing {', '.join(missing)}")
        if unsafe:
            failures.append(f"{path.name}: forbidden content {', '.join(unsafe[:10])}")
        reports.append(
            {
                "artifact": str(path),
                "distribution": distribution,
                "kind": kind,
                "file_count": len(members),
                "files": members,
                "missing": missing,
                "forbidden": unsafe,
                "passed": not missing and not unsafe,
            }
        )
    payload = {
        "schema_version": 1,
        "passed": not failures,
        "artifacts": reports,
        "errors": failures,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        args.output.chmod(0o600)
    print(rendered, end="")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
