#!/usr/bin/env python3
"""One command for the local stage/all verification gate."""

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
    parser.add_argument("stage", choices=("0", "1", "2", "3", "4", "5", "6", "7", "8", "all"))
    parser.add_argument("--hermes-checkout", type=Path)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    run(["ruff", "format", "--check", "."])
    run(["ruff", "check", "."])
    if shutil.which("mypy"):
        run(["mypy", "belief_ledger_pramana"])
    elif args.stage in {"8", "all"}:
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
        if not shutil.which("python") and sys.executable:
            pass
        run([sys.executable, "-m", "build"])
        if shutil.which("twine"):
            run(["twine", "check", *[str(path) for path in sorted((ROOT / "dist").glob("*"))]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
