from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from belief_ledger_pramana.contracts import EnforcementProfile, HostCapabilities


def test_profile_truth_table_is_capability_derived() -> None:
    capabilities = HostCapabilities(
        1,
        per_request_context=True,
        pre_action_gate=True,
        accepted_final_transform=True,
        tool_inventory=True,
    )
    assert capabilities.maximum_profile() is EnforcementProfile.ACCEPTED_FINAL
    assert capabilities.missing_for(EnforcementProfile.ACTION_ENFORCE) == ()
    assert set(capabilities.missing_for(EnforcementProfile.STRICT)) == {
        "atomic_action_token_consume",
        "exclusive_final_output_gate",
        "buffered_stream_delivery",
        "bound_approval",
    }


def test_deployment_lifecycle_is_byte_identical_across_clean_runs() -> None:
    command = [
        sys.executable,
        "examples/deployment_gate/run.py",
        "--format",
        "json",
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    second = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    assert first == second
    result = json.loads(first)
    assert [item["outcome"] for item in result["decisions"]] == [
        "block",
        "observed",
        "block",
        "approved",
        "allow",
        "retracted",
        "block",
    ]


def test_host_neutral_runtime_has_no_direct_nondeterministic_calls() -> None:
    root = Path("belief_ledger_pramana")
    forbidden = {
        ("datetime", "now"),
        ("time", "monotonic"),
        ("secrets", "token_urlsafe"),
        ("secrets", "token_bytes"),
    }
    for relative in ("contracts.py", "core_config.py", "core_runtime.py"):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        calls = {
            (node.func.value.id, node.func.attr)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        }
        assert not calls.intersection(forbidden), relative
