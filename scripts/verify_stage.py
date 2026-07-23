#!/usr/bin/env python3
"""One command for the complete local verification gate."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    environment = os.environ.copy()
    if os.name != "nt":
        environment.update({"TMPDIR": "/tmp", "TEMP": "/tmp", "TMP": "/tmp"})
    subprocess.run(command, cwd=ROOT, check=True, env=environment)


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

    run(["uv", "lock", "--check"])
    run([sys.executable, "-m", "ruff", "format", "--check", "."])
    run([sys.executable, "-m", "ruff", "check", "."])
    if shutil.which("mypy") or importlib.util.find_spec("mypy"):
        run(
            [
                sys.executable,
                "-m",
                "mypy",
                "packages/core/src",
                "packages/reference/src",
                "belief_ledger_pramana",
            ]
        )
    else:
        raise RuntimeError("mypy is required for the complete gate")
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not live_llm",
            "--cov=belief_ledger_core",
            "--cov=belief_ledger_pramana",
            "--cov=belief_ledger_reference",
            "--cov-branch",
            "--cov-report=term-missing",
            "--cov-report=xml:artifacts/coverage.xml",
            "--junitxml=artifacts/test-results.xml",
        ]
    )
    run([sys.executable, "scripts/check_dependency_boundaries.py"])
    run([sys.executable, "scripts/check_product_claims.py"])
    run([sys.executable, "examples/deployment_gate/validate_fixtures.py"])
    run(
        [
            sys.executable,
            "examples/deployment_gate/run.py",
            "--adapter",
            "reference",
            "--profile",
            "strict",
            "--format",
            "json",
        ]
    )
    run(
        [
            sys.executable,
            "-m",
            "evaluations.report",
            "--suite",
            "all",
            "--offline",
            "--output-dir",
            "build/evaluation",
        ]
    )
    run(
        [
            sys.executable,
            "-m",
            "belief_ledger_pramana.policy_cli",
            "policy",
            "validate",
            "--format",
            "json",
        ]
    )
    contract = [sys.executable, "scripts/check_hermes_contract.py"]
    if args.hermes_checkout:
        contract.extend(["--checkout", str(args.hermes_checkout)])
    else:
        contract.append("--allow-missing")
    run(contract)
    if not args.skip_build:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        manifest = ROOT / "build" / f"artifacts-{run_id}.json"
        run(
            [
                sys.executable,
                "scripts/build_workspace.py",
                "--packages",
                "core",
                "hermes",
                "reference",
                "--output-manifest",
                str(manifest),
            ]
        )
        run([sys.executable, "scripts/inspect_artifacts.py", "--manifest", str(manifest)])
        built = json.loads(manifest.read_text(encoding="utf-8"))["artifacts"]
        wheels = [str(item["path"]) for item in built]
        if shutil.which("twine") or importlib.util.find_spec("twine"):
            run([sys.executable, "-m", "twine", "check", *wheels])
        run(
            [
                sys.executable,
                "scripts/smoke_install.py",
                "--matrix",
                "core,core+reference,hermes",
                "--manifest",
                str(manifest),
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
