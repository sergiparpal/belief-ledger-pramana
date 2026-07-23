#!/usr/bin/env python3
"""Run the deployment gate through the deterministic normalized lifecycle."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from belief_ledger_reference import ReferenceRunner

from belief_ledger_pramana.contracts import (
    ApprovalResult,
    EnforcementProfile,
    EpisodeContext,
    HostCapabilities,
    ToolInvocation,
)
from belief_ledger_pramana.core_runtime import LedgerRuntime
from belief_ledger_pramana.dependencies import deterministic_dependencies
from belief_ledger_pramana.events import canonical_json, content_hash

ROOT = Path(__file__).resolve().parent


def run_fake() -> dict[str, Any]:
    scenario = json.loads((ROOT / "scenario-v1.json").read_text(encoding="utf-8"))
    episode = scenario["episode"]
    context = EpisodeContext.normalize(
        session_id=episode["session_id"],
        turn_id=episode["turn_id"],
        task_id=episode["task_id"],
        platform=episode["platform"],
        model="deterministic-model",
    )
    capabilities = HostCapabilities(
        1,
        per_request_context=True,
        pre_action_gate=True,
        atomic_action_token_consume=True,
        accepted_final_transform=True,
        exclusive_final_output_gate=True,
        buffered_stream_delivery=True,
        bound_approval=True,
        tool_inventory=True,
    )
    runtime = LedgerRuntime(
        ROOT / ".fixture-state",
        deterministic_dependencies(),
        capabilities,
        requested_profile=EnforcementProfile.STRICT,
    )
    runtime.start_episode(context)
    request = scenario["request"]
    invocation = ToolInvocation.normalize(context, request["tool"], request["arguments"])
    decisions: list[dict[str, Any]] = []

    first = runtime.authorize_deployment(invocation)
    decisions.append(
        {
            "step": 1,
            "outcome": first.outcome,
            "reason_code": first.reason_code,
            "missing": list(first.missing),
            "suggested_observation": first.suggested_observation,
        }
    )
    runtime.ingest_health("green")
    decisions.append({"step": 2, "outcome": "observed", "evidence": "health:production=green"})
    approval_missing = runtime.authorize_deployment(invocation)
    decisions.append(
        {
            "step": 3,
            "outcome": approval_missing.outcome,
            "reason_code": approval_missing.reason_code,
            "missing": list(approval_missing.missing),
        }
    )
    arguments_hash = content_hash(canonical_json(request["arguments"]))
    approval = ApprovalResult(
        1,
        context,
        True,
        "",
        "deploy",
        arguments_hash,
        "production",
        "deploy-production",
        "sha256:fixture-policy-v1",
        "exact_action",
    )
    runtime.record_approval(approval)
    binding = "deploy|production|artifact=app:2026.07.22|turn-001|sha256:fixture-policy-v1"
    decisions.append({"step": 4, "outcome": "approved", "approval_binding": binding})
    allowed = runtime.authorize_deployment(invocation)
    decisions.append({"step": 5, "outcome": allowed.outcome, "reason_code": allowed.reason_code})
    runtime.ingest_health("red")
    decisions.append(
        {
            "step": 6,
            "outcome": "retracted",
            "evidence": "health:production=red",
            "defeated": "health:production=green",
        }
    )
    blocked = runtime.authorize_deployment(invocation)
    decisions.append(
        {
            "step": 7,
            "outcome": blocked.outcome,
            "reason_code": blocked.reason_code,
            "missing": ["production health is green"],
        }
    )
    return {"schema_version": 1, "scenario": "deployment_gate", "decisions": decisions}


def run_reference() -> dict[str, Any]:
    scenario = json.loads((ROOT / "scenario-v1.json").read_text(encoding="utf-8"))
    episode = scenario["episode"]
    context = EpisodeContext.normalize(
        session_id=episode["session_id"],
        turn_id=episode["turn_id"],
        task_id=episode["task_id"],
        platform="reference-fixture",
        model="deterministic-model",
    )
    request = scenario["request"]
    invocation = ToolInvocation.normalize(context, request["tool"], request["arguments"])
    decisions: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="belief-ledger-reference-") as directory:
        runner = ReferenceRunner(Path(directory), dependencies=deterministic_dependencies())
        runner.start(context)
        first = runner.authorize(invocation)
        decisions.append(
            {
                "step": 1,
                "outcome": first.outcome,
                "reason_code": first.reason_code,
                "missing": list(first.missing),
                "suggested_observation": "Observe current production health with health_probe",
            }
        )
        runner.observe_health("green")
        decisions.append({"step": 2, "outcome": "observed", "evidence": "health:production=green"})
        approval_missing = runner.authorize(invocation)
        decisions.append(
            {
                "step": 3,
                "outcome": approval_missing.outcome,
                "reason_code": approval_missing.reason_code,
                "missing": list(approval_missing.missing),
            }
        )
        receipt = runner.approve_deployment(invocation)
        if receipt is None:
            raise RuntimeError("reference fixture approval was unexpectedly denied")
        binding = "deploy|production|artifact=app:2026.07.22|turn-001|sha256:fixture-policy-v1"
        decisions.append({"step": 4, "outcome": "approved", "approval_binding": binding})
        allowed = runner.authorize(invocation, approval=receipt)
        if allowed.permit is None or not runner.dispatch(invocation, allowed.permit).executed:
            raise RuntimeError("strict reference dispatch did not execute")
        decisions.append(
            {"step": 5, "outcome": allowed.outcome, "reason_code": allowed.reason_code}
        )
        runner.observe_health("red")
        decisions.append(
            {
                "step": 6,
                "outcome": "retracted",
                "evidence": "health:production=red",
                "defeated": "health:production=green",
            }
        )
        blocked = runner.authorize(invocation)
        decisions.append(
            {
                "step": 7,
                "outcome": blocked.outcome,
                "reason_code": blocked.reason_code,
                "missing": ["production health is green"],
            }
        )
    return {"schema_version": 1, "scenario": "deployment_gate", "decisions": decisions}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("fake", "reference"), default="fake")
    parser.add_argument("--profile", choices=tuple(EnforcementProfile), default="strict")
    parser.add_argument("--format", choices=("json", "human"), default="human")
    args = parser.parse_args()
    if args.profile != EnforcementProfile.STRICT:
        raise SystemExit("the deployment contract is a strict-profile fixture")
    result = run_reference() if args.adapter == "reference" else run_fake()
    expected = json.loads((ROOT / "expected-result-v1.json").read_text(encoding="utf-8"))
    if result != expected:
        raise RuntimeError("deterministic deployment result does not match its contract")
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        for decision in result["decisions"]:
            suffix = f" [{decision.get('reason_code')}]" if decision.get("reason_code") else ""
            print(f"{decision['step']}. {decision['outcome'].upper()}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
