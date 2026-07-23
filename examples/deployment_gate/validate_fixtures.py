#!/usr/bin/env python3
"""Validate the stable, versioned deployment-gate scenario contract."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    scenario = json.loads((ROOT / "scenario-v1.json").read_text(encoding="utf-8"))
    expected = json.loads((ROOT / "expected-result-v1.json").read_text(encoding="utf-8"))
    if scenario.get("schema_version") != 1 or expected.get("schema_version") != 1:
        raise ValueError("deployment fixtures must use schema version 1")
    policy = scenario["policy"]
    required = {"production_health_green", "exact_human_approval"}
    if set(policy["preconditions"]) != required or not policy["effectful"]:
        raise ValueError("deployment policy is incomplete")
    decisions = expected["decisions"]
    outcomes = [item["outcome"] for item in decisions]
    if outcomes != ["block", "observed", "block", "approved", "allow", "retracted", "block"]:
        raise ValueError("expected decision sequence is incomplete")
    if (
        decisions[0]["suggested_observation"]
        != "Observe current production health with health_probe"
    ):
        raise ValueError("first block must identify the next safe observation")
    binding_parts = decisions[3]["approval_binding"].split("|")
    if len(binding_parts) != 5 or binding_parts[0] != scenario["request"]["tool"]:
        raise ValueError("approval binding is not exact")
    print(json.dumps({"schema_version": 1, "valid": True, "steps": len(decisions)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
