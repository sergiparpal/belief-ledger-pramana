#!/usr/bin/env python3
"""Offline caller-defined customer-message gate using the generic reference runner."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from belief_ledger_core import (
    ApprovalResult,
    EpisodeContext,
    EvidenceObservation,
    ToolDescriptor,
    ToolInvocation,
    deterministic_dependencies,
)
from belief_ledger_core.events import canonical_json, content_hash
from belief_ledger_reference import ReferenceRunner

ROOT = Path(__file__).resolve().parent


def run() -> dict[str, Any]:
    context = EpisodeContext.normalize(
        session_id="custom-tool-demo",
        turn_id="turn-1",
        task_id="contact-customer",
        platform="reference-example",
        model="deterministic",
    )
    sent: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="belief-ledger-custom-") as directory:
        runner = ReferenceRunner(Path(directory), dependencies=deterministic_dependencies())
        runner.register_tool(
            ToolDescriptor.create(
                "send_customer_message",
                {
                    "type": "object",
                    "properties": {
                        "recipient": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["recipient", "body"],
                },
                namespace="crm",
            ),
            lambda arguments: sent.append(dict(arguments)) or {"accepted": True},
            effectful=True,
            policy={
                "id": "customer-message-v1",
                "revision": "policy-v1",
                "effectful": True,
                "base_stakes": "high",
                "target_fields": ["recipient"],
                "preconditions": ["recipient_identity"],
                "approval_policy": "required",
                "minimum_source_integrity": "trusted",
                "canonicalization_version": 1,
            },
        )
        runner.start(context)
        invocation = ToolInvocation.normalize(
            context,
            "send_customer_message",
            {"recipient": "customer-42", "body": "Your replacement shipped."},
            namespace="crm",
        )
        first = runner.authorize(invocation)
        evidence = runner.ingest_evidence(
            EvidenceObservation.normalize(
                "Precondition recipient_identity holds for customer-42",
                source_name="customer_directory",
                source_kind="tool",
                source_integrity="trusted",
                provenance_root="directory:customer-42",
                target="customer-42",
            )
        )
        approval_needed = runner.authorize(invocation)
        runner.record_approval(
            ApprovalResult(
                1,
                context,
                True,
                "crm",
                "send_customer_message",
                content_hash(canonical_json(invocation.arguments_dict())),
                "customer-42",
                "customer-message-v1",
                "policy-v1",
                "exact_action",
            )
        )
        allowed = runner.authorize(invocation)
        dispatched = runner.dispatch(invocation, allowed.permit)
        runner.retract_support(evidence.belief_id)
        blocked_again = runner.authorize(invocation)
        result = {
            "schema_version": 1,
            "scenario": "custom_tool_gate",
            "steps": [
                {"outcome": first.outcome, "reason_code": first.reason_code},
                {"outcome": "observed", "reason_code": evidence.reason_code},
                {"outcome": approval_needed.outcome, "reason_code": approval_needed.reason_code},
                {
                    "outcome": allowed.outcome,
                    "reason_code": allowed.reason_code,
                    "executed": dispatched.executed,
                },
                {"outcome": "retracted", "reason_code": "SUPPORT_RETRACTED"},
                {"outcome": blocked_again.outcome, "reason_code": blocked_again.reason_code},
            ],
            "effects": len(sent),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "human"), default="human")
    args = parser.parse_args()
    result = run()
    expected = json.loads((ROOT / "expected-result-v1.json").read_text(encoding="utf-8"))
    if result != expected:
        raise RuntimeError("custom tool result does not match its deterministic fixture")
    if args.format == "json":
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        for index, step in enumerate(result["steps"], 1):
            print(f"{index}. {step['outcome'].upper()} [{step['reason_code']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
