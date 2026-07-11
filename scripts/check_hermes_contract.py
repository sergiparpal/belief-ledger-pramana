#!/usr/bin/env python3
"""Verify the pinned Hermes source/runtime capabilities without network access."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any

AUDITED_VERSION = "0.18.2"
AUDITED_COMMIT = "3b2ef789dfcf92f5b7b18c08c59d25948e50857f"


def check_checkout(path: Path) -> dict[str, Any]:
    required = {
        "register_tool": ("hermes_cli/plugins.py", "def register_tool("),
        "register_hook": ("hermes_cli/plugins.py", "def register_hook("),
        "register_middleware": ("hermes_cli/plugins.py", "def register_middleware("),
        "register_command": ("hermes_cli/plugins.py", "def register_command("),
        "register_cli_command": ("hermes_cli/plugins.py", "def register_cli_command("),
        "llm_request": ("hermes_cli/middleware.py", 'LLM_REQUEST_MIDDLEWARE = "llm_request"'),
        "transform_llm_output": ("hermes_cli/plugins.py", '"transform_llm_output"'),
        "approval_escalation": ("hermes_cli/plugins.py", 'action not in ("block", "approve")'),
    }
    capabilities: dict[str, bool] = {}
    for name, (relative, needle) in required.items():
        file_path = path / relative
        capabilities[name] = file_path.is_file() and needle in file_path.read_text(encoding="utf-8")
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    try:
        project = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
        version = str(project["project"]["version"])
        python_range = str(project["project"]["requires-python"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        version = None
        python_range = None
    ok = commit == AUDITED_COMMIT and version == AUDITED_VERSION and all(capabilities.values())
    return {
        "ok": ok,
        "source": str(path),
        "expected_version": AUDITED_VERSION,
        "observed_version": version,
        "expected_commit": AUDITED_COMMIT,
        "observed_commit": commit,
        "requires_python": python_range,
        "capabilities": capabilities,
    }


def check_installed() -> dict[str, Any]:
    try:
        version = importlib.metadata.version("hermes-agent")
    except importlib.metadata.PackageNotFoundError:
        version = None
    capabilities: dict[str, bool] = {}
    if version:
        try:
            from hermes_cli.middleware import VALID_MIDDLEWARE
            from hermes_cli.plugins import VALID_HOOKS, PluginContext

            capabilities = {
                "register_tool": hasattr(PluginContext, "register_tool"),
                "register_hook": hasattr(PluginContext, "register_hook"),
                "register_middleware": hasattr(PluginContext, "register_middleware"),
                "llm_request": "llm_request" in VALID_MIDDLEWARE,
                "transform_llm_output": "transform_llm_output" in VALID_HOOKS,
            }
        except ImportError:
            capabilities = {"host_import": False}
    return {
        "ok": version == AUDITED_VERSION and bool(capabilities) and all(capabilities.values()),
        "source": "installed-distribution",
        "expected_version": AUDITED_VERSION,
        "observed_version": version,
        "expected_commit": AUDITED_COMMIT,
        "observed_commit": None,
        "capabilities": capabilities,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--checkout",
        type=Path,
        default=Path(os.environ["HERMES_AUDIT_CHECKOUT"])
        if os.environ.get("HERMES_AUDIT_CHECKOUT")
        else None,
    )
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument(
        "--allow-version-drift",
        action="store_true",
        help="Canary mode: require capabilities while reporting version/commit drift",
    )
    args = parser.parse_args()
    report = check_checkout(args.checkout.resolve()) if args.checkout else check_installed()
    print(json.dumps(report, indent=2, sort_keys=True))
    capabilities_ok = bool(report["capabilities"]) and all(report["capabilities"].values())
    accepted = (
        report["ok"]
        or (args.allow_missing and report["observed_version"] is None)
        or (args.allow_version_drift and capabilities_ok)
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
