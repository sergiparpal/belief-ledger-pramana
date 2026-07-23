from __future__ import annotations

import argparse
import json

from belief_ledger_pramana.hermes.cli import run_cli, setup_cli


def _run(runtime, *arguments: str):
    parser = argparse.ArgumentParser()
    setup_cli(parser)
    return run_cli(runtime, parser.parse_args(list(arguments)))


def test_policy_commands_have_stable_json_and_inactive_scaffolds(runtime) -> None:
    runtime.ctx.tools["read_file"] = {
        "schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "description": "Read one file",
        "toolset": "workspace",
    }
    code, output = _run(runtime, "policy", "validate", "--format", "json")
    assert code == 0
    assert json.loads(output)["normalized_schema_version"] == 2
    code, output = _run(runtime, "policy", "inventory", "--format", "json")
    inventory = json.loads(output)
    assert code == 0
    assert inventory["complete"] is False
    assert inventory["reason_code"] == "HERMES_COMPLETE_INVENTORY_UNPROVEN"
    code, output = _run(runtime, "policy", "scaffold", "new_mutation", "--format", "json")
    scaffold = json.loads(output)
    assert code == 0
    assert scaffold["review_required"] is True
    assert scaffold["rule"]["active"] is False
    code, output = _run(runtime, "policy", "explain", "read_file", "--format", "json")
    assert code == 0
    assert json.loads(output)["policy"]["effectful"] is False
