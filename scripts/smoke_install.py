#!/usr/bin/env python3
"""Install a wheel/sdist without dependencies and verify entry-point import."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import venv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--skip-hermes",
        action="store_true",
        help="Only verify the entry point; the release gate must not use this option",
    )
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    with tempfile.TemporaryDirectory() as directory:
        environment = Path(directory) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / (
            "Scripts/python.exe" if (environment / "Scripts").exists() else "bin/python"
        )
        command_environment = os.environ.copy()
        command_environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        if not args.skip_hermes:
            subprocess.run(
                [str(python), "-m", "pip", "install", "hermes-agent==0.18.2"],
                check=True,
                env=command_environment,
            )
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(artifact)],
            check=True,
            env=command_environment,
        )
        home = Path(directory) / "hermes-home"
        home.mkdir()
        home.joinpath("config.yaml").write_text(
            "plugins:\n  enabled: [belief-ledger-pramana]\n  disabled: []\n",
            encoding="utf-8",
        )
        if args.skip_hermes:
            code = (
                "import importlib.metadata,json;"
                "eps=importlib.metadata.entry_points().select(group='hermes_agent.plugins');"
                "ep=next(x for x in eps if x.name=='belief-ledger-pramana');"
                "m=ep.load();print(json.dumps({'module':m.__name__,"
                "'version':importlib.metadata.version('belief-ledger-pramana'),"
                "'register':callable(m.register)}))"
            )
        else:
            code = (
                "import importlib.metadata,json;from hermes_cli.plugins import PluginManager;"
                "m=PluginManager();m.discover_and_load();p=m._plugins['belief-ledger-pramana'];"
                "print(json.dumps({'module':p.module.__name__ if p.module else '',"
                "'version':importlib.metadata.version('belief-ledger-pramana'),"
                "'hermes':importlib.metadata.version('hermes-agent'),'register':p.enabled,"
                "'tools':sorted(x for x in m._plugin_tool_names if x.startswith('pramana_')),"
                "'middleware':sorted(m._middleware),'hooks':sorted(m._hooks)}))"
            )
        command_environment["HERMES_HOME"] = str(home)
        result = subprocess.run(
            [str(python), "-c", code],
            check=True,
            capture_output=True,
            text=True,
            env=command_environment,
            timeout=60,
        )
        report = json.loads(result.stdout)
        print(json.dumps(report, indent=2, sort_keys=True))
    expected = report["register"] and report["version"] == "1.0.0rc1"
    if not args.skip_hermes:
        expected = expected and report["hermes"] == "0.18.2"
        expected = expected and report["middleware"] == ["llm_request"]
        expected = expected and len(report["tools"]) == 4
    return 0 if expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
