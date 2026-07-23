from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

EXPECTED_HOOKS = {
    "pre_llm_call",
    "pre_tool_call",
    "transform_tool_result",
    "transform_llm_output",
    "post_llm_call",
    "pre_verify",
    "on_session_start",
    "on_session_end",
    "on_session_finalize",
    "on_session_reset",
    "subagent_start",
    "subagent_stop",
    "post_approval_response",
}
EXPECTED_TOOLS = {
    "pramana_record_inference",
    "pramana_query",
    "pramana_explain",
    "pramana_request_verification",
}


def _run_manager(home: Path, *, without_entrypoints: bool = False) -> dict[str, object]:
    patch = "PluginManager._scan_entry_points=lambda self: [];" if without_entrypoints else ""
    # Keep this isolated: Hermes' global tool registry intentionally rejects a
    # second registration of the same names in one process.
    code = (
        "import importlib.metadata,json;"
        "from hermes_cli.plugins import PluginManager;"
        + patch
        + "m=PluginManager();m.discover_and_load();"
        "p=m._plugins['belief-ledger-pramana'];"
        "print(json.dumps({'version':importlib.metadata.version('hermes-agent'),"
        "'enabled':p.enabled,'error':p.error,'source':p.manifest.source,"
        "'tools':sorted(set(m._plugin_tool_names)&"
        + repr(EXPECTED_TOOLS)
        + "),'hooks':sorted(m._hooks),'middleware':sorted(m._middleware),"
        "'commands':sorted(m._plugin_commands),'cli':sorted(m._cli_commands)}))"
    )
    environment = os.environ.copy()
    environment["HERMES_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=home,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _write_activation(home: Path, *, enabled: bool) -> None:
    state = "enabled" if enabled else "disabled"
    (home / "config.yaml").write_text(
        f"plugins:\n  enabled: {['belief-ledger-pramana'] if enabled else []}\n"
        f"  disabled: {['belief-ledger-pramana'] if not enabled else []}\n"
        f"# expected state: {state}\n",
        encoding="utf-8",
    )


@pytest.mark.contract
def test_real_pinned_entrypoint_manager_enable_and_disable(tmp_path: Path) -> None:
    _write_activation(tmp_path, enabled=True)
    enabled = _run_manager(tmp_path)
    assert enabled["version"] == "0.18.2"
    assert enabled["enabled"] is True
    assert enabled["source"] == "entrypoint"
    assert set(enabled["tools"]) == EXPECTED_TOOLS
    assert set(enabled["hooks"]) >= EXPECTED_HOOKS
    assert enabled["middleware"] == ["llm_request"]
    assert enabled["commands"] == ["ledger"]
    assert enabled["cli"] == ["belief-ledger"]

    _write_activation(tmp_path, enabled=False)
    disabled = _run_manager(tmp_path)
    assert disabled["enabled"] is False
    assert disabled["tools"] == []
    assert disabled["error"] == "disabled via config"


@pytest.mark.contract
def test_real_pinned_manager_loads_directory_layout() -> None:
    # A deeply nested checkout plus pytest's per-test directory can exceed
    # MAX_PATH before Hermes sees the layout. Use the system's short temp root
    # for this copy-based host contract test.
    with tempfile.TemporaryDirectory(prefix="blp-") as temporary:
        home = Path(temporary)
        root = Path(__file__).parents[2]
        plugin_dir = home / "plugins" / "belief-ledger-pramana"
        plugin_dir.mkdir(parents=True)
        for name in ("plugin.yaml", "__init__.py", "after-install.md"):
            shutil.copy2(root / name, plugin_dir / name)
        shutil.copytree(root / "belief_ledger_pramana", plugin_dir / "belief_ledger_pramana")
        shutil.copytree(
            root / "packages" / "core" / "src",
            plugin_dir / "packages" / "core" / "src",
        )
        _write_activation(home, enabled=True)
        report = _run_manager(home, without_entrypoints=True)
        assert report["version"] == "0.18.2"
        assert report["enabled"] is True
        assert report["source"] == "user"
        assert set(report["tools"]) == EXPECTED_TOOLS
        assert set(report["hooks"]) == EXPECTED_HOOKS
