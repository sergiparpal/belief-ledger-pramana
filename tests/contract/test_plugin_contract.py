from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
import types
from pathlib import Path

from belief_ledger_pramana.plugin import register

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


def test_register_exposes_full_declared_surface(fake_ctx) -> None:
    register(fake_ctx)
    assert set(fake_ctx.tools) == EXPECTED_TOOLS
    assert set(fake_ctx.hooks) == EXPECTED_HOOKS
    assert set(fake_ctx.middleware) == {"llm_request"}
    assert set(fake_ctx.commands) == {"ledger"}
    assert set(fake_ctx.cli_commands) == {"belief-ledger"}
    assert all(callable(item["handler"]) for item in fake_ctx.tools.values())
    register(fake_ctx)
    assert all(len(callbacks) == 1 for callbacks in fake_ctx.hooks.values())


def test_every_tool_accepts_unknown_host_kwargs_and_returns_json(
    fake_ctx, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    register(fake_ctx)
    arguments = {
        "pramana_record_inference": {},
        "pramana_query": {"query": "anything"},
        "pramana_explain": {"belief_id": "not-an-id"},
        "pramana_request_verification": {
            "belief_id": "not-an-id",
            "method": "cross_source",
        },
    }
    for name, tool in fake_ctx.tools.items():
        result = tool["handler"](
            arguments[name],
            session_id="s",
            turn_id="t",
            future_host_kwarg={"x": 1},
        )
        parsed = json.loads(result)
        assert isinstance(parsed["ok"], bool)


def test_directory_plugin_shim_loads_under_generated_namespace(fake_ctx) -> None:
    root = Path(__file__).parents[2]
    parent_name = "hermes_plugins"
    module_name = "hermes_plugins.belief_ledger_pramana_contract"
    parent = types.ModuleType(parent_name)
    parent.__path__ = []
    sys.modules.setdefault(parent_name, parent)
    spec = importlib.util.spec_from_file_location(
        module_name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module.register(fake_ctx)
    assert set(fake_ctx.tools) == EXPECTED_TOOLS


def test_entry_point_loads_a_module_not_a_function() -> None:
    root = Path(__file__).parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    value = data["project"]["entry-points"]["hermes_agent.plugins"]["belief-ledger-pramana"]
    assert value == "belief_ledger_pramana.plugin"
    assert ":" not in value


def test_manifest_matches_registration() -> None:
    import yaml

    root = Path(__file__).parents[2]
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    assert set(manifest["provides_tools"]) == EXPECTED_TOOLS
    assert set(manifest["provides_hooks"]) == EXPECTED_HOOKS
    assert manifest["manifest_version"] == 1
