#!/usr/bin/env python3
"""Run the deployment gate through the deterministic normalized lifecycle."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from belief_ledger_core import (
    ApprovalResult,
    EnforcementProfile,
    EpisodeContext,
    EvidenceObservation,
    HostCapabilities,
    ToolDescriptor,
    ToolInvocation,
    deterministic_dependencies,
)
from belief_ledger_core.events import canonical_json, content_hash
from belief_ledger_core.runtime import LedgerRuntime
from belief_ledger_reference import ReferenceRunner

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
        deployments: list[dict[str, Any]] = []
        runner.register_tool(
            ToolDescriptor.create(
                "health_probe",
                {"type": "object", "properties": {"environment": {"type": "string"}}},
            ),
            lambda arguments: {"environment": arguments.get("environment", "production")},
            effectful=False,
            policy={
                "id": "health-observation",
                "revision": "sha256:fixture-health-v1",
                "effectful": False,
                "base_stakes": "med",
                "target_fields": ["environment"],
                "preconditions": [],
                "approval_policy": "none",
                "minimum_source_integrity": "untrusted",
                "canonicalization_version": 1,
            },
        )

        def deploy(arguments: dict[str, Any]) -> dict[str, Any]:
            deployments.append(dict(arguments))
            return dict(arguments)

        runner.register_tool(
            ToolDescriptor.create(
                "deploy",
                {
                    "type": "object",
                    "properties": {
                        "artifact": {"type": "string"},
                        "environment": {"type": "string"},
                    },
                },
            ),
            deploy,
            effectful=True,
            policy={
                "id": "deploy-production",
                "revision": "sha256:fixture-policy-v1",
                "effectful": True,
                "base_stakes": "high",
                "target_fields": ["environment"],
                "preconditions": ["production_health_green"],
                "approval_policy": "required",
                "minimum_source_integrity": "trusted",
                "canonicalization_version": 1,
            },
        )
        runner.start(context)
        first = runner.authorize(invocation)
        decisions.append(
            {
                "step": 1,
                "outcome": first.outcome,
                "reason_code": first.reason_code,
                "missing": ["production health is green", "exact human approval"],
                "suggested_observation": "Observe current production health with health_probe",
            }
        )
        admitted = runner.ingest_evidence(
            EvidenceObservation.normalize(
                "Precondition production_health_green holds for production",
                source_name="health_probe",
                source_kind="tool",
                source_integrity="trusted",
                provenance_root="tool:health_probe:production",
                target="production",
            )
        )
        decisions.append({"step": 2, "outcome": "observed", "evidence": "health:production=green"})
        approval_missing = runner.authorize(invocation)
        decisions.append(
            {
                "step": 3,
                "outcome": "block",
                "reason_code": approval_missing.reason_code,
                "missing": ["exact human approval"],
            }
        )
        approval = ApprovalResult(
            1,
            context,
            True,
            "",
            "deploy",
            content_hash(canonical_json(request["arguments"])),
            "production",
            "deploy-production",
            "sha256:fixture-policy-v1",
            "exact_action",
        )
        if runner.record_approval(approval) != "APPROVAL_RECORDED":
            raise RuntimeError("reference fixture approval was unexpectedly denied")
        binding = "deploy|production|artifact=app:2026.07.22|turn-001|sha256:fixture-policy-v1"
        decisions.append({"step": 4, "outcome": "approved", "approval_binding": binding})
        allowed = runner.authorize(invocation)
        if allowed.permit is None or not runner.dispatch(invocation, allowed.permit).executed:
            raise RuntimeError("strict reference dispatch did not execute")
        decisions.append(
            {"step": 5, "outcome": allowed.outcome, "reason_code": allowed.reason_code}
        )
        runner.retract_support(admitted.belief_id)
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
                "reason_code": "SUPPORT_RETRACTED",
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
