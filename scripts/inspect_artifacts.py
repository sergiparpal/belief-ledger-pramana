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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = []
    failures: list[str] = []
    forbidden = ("__pycache__/", ".pyc", ".sqlite3", ".coverage", ".venv/")
    for raw_path in args.artifacts:
        path = raw_path.resolve()
        members = _members(path)
        kind = "wheel" if path.suffix == ".whl" else "sdist"
        required = [
            "belief_ledger_pramana/plugin.py",
            "belief_ledger_pramana/data/defaults.yaml",
            "belief_ledger_pramana/data/action-policies.yaml",
            "belief_ledger_pramana/data/source-profiles.yaml",
            "belief_ledger_pramana/data/migrations/0001_initial.sql",
        ]
        if kind == "wheel":
            required.extend((".dist-info/METADATA", ".dist-info/entry_points.txt"))
        else:
            required.extend(("plugin.yaml", "README.md", "docs/operations.md", "tests/conftest.py"))
        missing = [item for item in required if not _contains(members, item)]
        unsafe = [member for member in members if any(token in member for token in forbidden)]
        if missing:
            failures.append(f"{path.name}: missing {', '.join(missing)}")
        if unsafe:
            failures.append(f"{path.name}: forbidden content {', '.join(unsafe[:10])}")
        reports.append(
            {
                "artifact": str(path),
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
