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
    parser.add_argument("artifact", type=Path, nargs="?")
    parser.add_argument("--matrix")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--skip-hermes",
        action="store_true",
        help="Only verify the entry point; the release gate must not use this option",
    )
    args = parser.parse_args()
    if args.matrix:
        if args.manifest is None:
            parser.error("--matrix requires --manifest")
        return _run_matrix(args.matrix, args.manifest)
    if args.artifact is None:
        parser.error("artifact is required without --matrix")
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
    expected = report["register"] and report["version"] == "1.0.0rc2"
    if not args.skip_hermes:
        expected = expected and report["hermes"] == "0.18.2"
        expected = expected and report["middleware"] == ["llm_request"]
        expected = expected and len(report["tools"]) == 4
    return 0 if expected else 1


def _run_matrix(matrix: str, manifest_path: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = {item["package"]: Path(item["path"]).resolve() for item in manifest["artifacts"]}
    reports: list[dict[str, object]] = []
    for mode in matrix.split(","):
        required = {
            "core": ("core",),
            "core+reference": ("core", "reference"),
            "hermes": ("core", "hermes"),
        }.get(mode)
        if required is None:
            raise ValueError(f"unknown smoke matrix mode: {mode}")
        missing = [name for name in required if name not in artifacts]
        if missing:
            raise ValueError(f"manifest lacks artifacts for {mode}: {', '.join(missing)}")
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment / (
                "Scripts/python.exe" if (environment / "Scripts").exists() else "bin/python"
            )
            command_environment = os.environ.copy()
            command_environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            if mode == "hermes":
                subprocess.run(
                    [str(python), "-m", "pip", "install", "hermes-agent==0.18.2"],
                    check=True,
                    env=command_environment,
                    capture_output=True,
                )
            for name in required:
                subprocess.run(
                    [str(python), "-m", "pip", "install", "--no-deps", str(artifacts[name])],
                    check=True,
                    env=command_environment,
                    capture_output=True,
                )
            if mode == "core":
                code = (
                    "import importlib.resources;import belief_ledger_core as m;"
                    "import belief_ledger_core.models,belief_ledger_core.store;"
                    "import belief_ledger_core.engine.defeat,belief_ledger_core.gate.decision;"
                    "p=importlib.resources.files('belief_ledger_core.data').joinpath('defaults.yaml');"
                    "assert p.is_file();print(m.__version__)"
                )
            elif mode == "core+reference":
                code = "import belief_ledger_core,belief_ledger_reference as m;print(m.__version__)"
            else:
                code = (
                    "import importlib.metadata;import belief_ledger_core;"
                    "eps=importlib.metadata.entry_points().select(group='hermes_agent.plugins');"
                    "ep=next(x for x in eps if x.name=='belief-ledger-pramana');"
                    "assert callable(ep.load().register);"
                    "assert importlib.metadata.version('hermes-agent')=='0.18.2';print('1.0.0rc2')"
                )
            result = subprocess.run(
                [str(python), "-c", code],
                check=True,
                capture_output=True,
                text=True,
                env=command_environment,
            )
            reports.append({"mode": mode, "version": result.stdout.strip(), "passed": True})
    print(json.dumps({"schema_version": 1, "reports": reports, "passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
