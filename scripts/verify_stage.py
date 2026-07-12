#!/usr/bin/env python3
"""One command for the complete local verification gate."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, env=os.environ.copy())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        nargs="?",
        choices=("all",),
        default="all",
        help="retained for backwards-compatible `all` invocations",
    )
    parser.add_argument("--hermes-checkout", type=Path)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    run(["ruff", "format", "--check", "."])
    run(["ruff", "check", "."])
    if shutil.which("mypy"):
        run(["mypy", "belief_ledger_pramana"])
    else:
        raise RuntimeError("mypy is required for the complete gate")
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not live_llm",
            "--cov=belief_ledger_pramana",
            "--cov-report=term-missing",
        ]
    )
    contract = [sys.executable, "scripts/check_hermes_contract.py"]
    if args.hermes_checkout:
        contract.extend(["--checkout", str(args.hermes_checkout)])
    else:
        contract.append("--allow-missing")
    run(contract)
    if not args.skip_build:
        run([sys.executable, "-m", "build"])
        if shutil.which("twine"):
            run(["twine", "check", *[str(path) for path in sorted((ROOT / "dist").glob("*"))]])
        artifacts = [str(path) for path in sorted((ROOT / "dist").glob("*"))]
        run([sys.executable, "scripts/inspect_artifacts.py", *artifacts])
        wheels = [str(path) for path in sorted((ROOT / "dist").glob("*.whl"))]
        if len(wheels) != 1:
            raise RuntimeError("expected exactly one wheel for install smoke test")
        run([sys.executable, "scripts/smoke_install.py", wheels[0]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
