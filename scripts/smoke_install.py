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

# Mirrors the override every CI job applies after installing the audited Hermes host.
# cryptography is held at >=50 because PYSEC-2026-3552 affects 49.x; the ceiling exists
# only to keep the override deliberate, so raise both bounds together when 51 lands.
_HERMES_SECURITY_OVERRIDES = ("Pillow>=12.3,<13", "cryptography>=50.0.0,<51")


def _temporary_directory() -> tempfile.TemporaryDirectory[str]:
    """Use the native temporary filesystem for Linux clean-install checks.

    WSL sessions can inherit a Windows-mounted ``TEMP`` directory. SQLite setup in
    the smoke programs then exercises cross-filesystem semantics instead of the
    target platform, so select ``/tmp`` explicitly outside Windows.
    """

    return tempfile.TemporaryDirectory(dir=None if os.name == "nt" else "/tmp")


def _command_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    if os.name != "nt":
        environment.update({"TMPDIR": "/tmp", "TEMP": "/tmp", "TMP": "/tmp"})
    return environment


def _install_hermes_host(
    python: Path,
    environment: dict[str, str],
    *,
    capture_output: bool = False,
) -> None:
    """Install the audited host plus the vulnerability-remediated leaf versions."""

    subprocess.run(
        [str(python), "-m", "pip", "install", "hermes-agent==0.19.0"],
        check=True,
        env=environment,
        capture_output=capture_output,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", *_HERMES_SECURITY_OVERRIDES],
        check=True,
        env=environment,
        capture_output=capture_output,
    )


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
    with _temporary_directory() as directory:
        environment = Path(directory) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / (
            "Scripts/python.exe" if (environment / "Scripts").exists() else "bin/python"
        )
        command_environment = _command_environment()
        if not args.skip_hermes:
            _install_hermes_host(python, command_environment)
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
            cwd=home,
            timeout=60,
        )
        report = json.loads(result.stdout)
        print(json.dumps(report, indent=2, sort_keys=True))
    expected = report["register"] and report["version"] == "1.0.0rc4"
    if not args.skip_hermes:
        expected = expected and report["hermes"] == "0.19.0"
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
            "core+gateway": ("core", "gateway"),
            "core+reference": ("core", "reference"),
            "core+gateway+mcp": ("core", "gateway", "mcp"),
            "hermes": ("core", "gateway", "hermes"),
        }.get(mode)
        if required is None:
            raise ValueError(f"unknown smoke matrix mode: {mode}")
        missing = [name for name in required if name not in artifacts]
        if missing:
            raise ValueError(f"manifest lacks artifacts for {mode}: {', '.join(missing)}")
        with _temporary_directory() as directory:
            environment = Path(directory) / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment / (
                "Scripts/python.exe" if (environment / "Scripts").exists() else "bin/python"
            )
            command_environment = _command_environment()
            if mode == "hermes":
                _install_hermes_host(python, command_environment, capture_output=True)
            if mode == "hermes":
                home = Path(directory) / "hermes-home"
                home.mkdir()
                home.joinpath("config.yaml").write_text(
                    "plugins:\n  enabled: [belief-ledger-pramana]\n  disabled: []\n",
                    encoding="utf-8",
                )
                command_environment["HERMES_HOME"] = str(home)
            target = artifacts["hermes"] if mode == "hermes" else artifacts[required[-1]]
            subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--find-links",
                    str(target.parent),
                    str(target),
                ],
                check=True,
                env=command_environment,
                capture_output=True,
            )
            if mode == "core":
                code = (
                    "import importlib.resources,importlib.util;import belief_ledger_core as m;"
                    "import belief_ledger_core.models,belief_ledger_core.store;"
                    "import belief_ledger_core.engine.defeat,belief_ledger_core.gate.decision;"
                    "p=importlib.resources.files('belief_ledger_core.data').joinpath('defaults.yaml');"
                    "assert p.is_file();"
                    "assert all(importlib.util.find_spec(x) is None for x in "
                    "('belief_ledger_gateway','belief_ledger_reference','belief_ledger_mcp',"
                    "'belief_ledger_pramana'));print(m.__version__)"
                )
            elif mode == "core+reference":
                code = "import belief_ledger_core,belief_ledger_reference as m;print(m.__version__)"
            elif mode == "core+gateway":
                code = (
                    "import importlib.resources,importlib.util,subprocess,sys;from pathlib import Path;"
                    "import belief_ledger_gateway as m;"
                    "c=Path(sys.executable).with_name('belief-ledger');assert c.is_file();"
                    "subprocess.run([str(c),'--help'],check=True,capture_output=True,text=True);"
                    "r=subprocess.run([str(c),'demo','--format','json'],check=True,capture_output=True,text=True);"
                    "p=importlib.resources.files('belief_ledger_core.data').joinpath('defaults.yaml');"
                    "assert p.is_file() and importlib.util.find_spec('belief_ledger_pramana') is None;"
                    'assert \'"profile":"observe"\' in r.stdout;print(m.__version__)'
                )
            elif mode == "core+gateway+mcp":
                code = (
                    "import tempfile;from pathlib import Path;from types import SimpleNamespace;"
                    "from belief_ledger_core import BeliefLedger,EnforcementProfile,EpisodeContext,"
                    "HostCapabilities;"
                    "from belief_ledger_mcp import BeliefLedgerMcp,McpMode,UpstreamCallResult,UpstreamTool;"
                    "t=UpstreamTool(1,'lookup','offline lookup',{'type':'object'},'local');d=t.descriptor();"
                    "u=SimpleNamespace(list_tools=lambda:(t,),"
                    "call_tool=lambda name,arguments,namespace='',correlation=None:"
                    "UpstreamCallResult(1,b'{\"offline\":true}',False,'success'));"
                    "mf={'schema_version':2,'rules':[{'id':'lookup','revision':'v1',"
                    "'effectful':False,'base_stakes':'low','exact':['lookup'],'namespace':'local',"
                    "'target_fields':[],'preconditions':[],'approval_policy':'none',"
                    "'minimum_source_integrity':'untrusted','canonicalization_version':1,"
                    "'input_schema_digest':d.schema_digest}]};"
                    "l=BeliefLedger.open(state_root=Path(tempfile.mkdtemp()),manifest=mf,"
                    "capabilities=HostCapabilities(pre_action_gate=True),"
                    "requested_profile=EnforcementProfile.ACTION_ENFORCE);"
                    "c=EpisodeContext.normalize(session_id='smoke',turn_id='1');e=l.start_episode(c);"
                    "a=BeliefLedgerMcp(l,mode=McpMode.PROXY,upstream=u,inventory_complete=True);"
                    "r=a.invoke(e.id,c,'lookup',{},namespace='local');"
                    "assert a.capability_profile=='action_enforce' and r.forwarded;"
                    "assert r.content==b'{\"offline\":true}';print('1.0.0rc4')"
                )
            else:
                code = (
                    "import importlib.metadata,subprocess,sys;from pathlib import Path;"
                    "import belief_ledger_core;from hermes_cli.plugins import PluginManager;"
                    "eps=importlib.metadata.entry_points().select(group='hermes_agent.plugins');"
                    "ep=next(x for x in eps if x.name=='belief-ledger-pramana');"
                    "assert callable(ep.load().register);"
                    "assert importlib.metadata.version('hermes-agent')=='0.19.0';"
                    "assert importlib.metadata.version('belief-ledger-gateway')=='1.0.0rc4';"
                    "c=Path(sys.executable).with_name('belief-ledger');assert c.is_file();"
                    "subprocess.run([str(c),'--help'],check=True,capture_output=True,text=True);"
                    "m=PluginManager();m.discover_and_load();"
                    "assert m._plugins['belief-ledger-pramana'].enabled;"
                    "assert sorted(x for x in m._plugin_tool_names if x.startswith('pramana_'))"
                    "==['pramana_explain','pramana_query','pramana_record_inference',"
                    "'pramana_request_verification'];"
                    "assert sorted(m._middleware)==['llm_request'];"
                    "print('1.0.0rc4')"
                )
            result = subprocess.run(
                [str(python), "-c", code],
                capture_output=True,
                text=True,
                env=command_environment,
                cwd=directory,
            )
            if result.returncode:
                raise RuntimeError(
                    f"{mode} smoke failed with exit {result.returncode}\n"
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )
            reports.append({"mode": mode, "version": result.stdout.strip(), "passed": True})
    print(json.dumps({"schema_version": 1, "reports": reports, "passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
