"""Versioned JSONL protocol for the standalone reference adapter."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from belief_ledger_core.contracts import EpisodeContext, ToolInvocation

from .runner import ReferenceRunner


def _constant_lint(accepted: bool) -> Callable[[str], bool]:
    def lint(text: str) -> bool:
        del text
        return accepted

    return lint


def serve_jsonl(source: TextIO, destination: TextIO, *, state_root: Path) -> int:
    runner = ReferenceRunner(state_root)
    context: EpisodeContext | None = None
    invocation: ToolInvocation | None = None
    for line_number, line in enumerate(source, 1):
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("schema_version") != 1:
                raise ValueError("unsupported request schema")
            operation = str(request.get("op", ""))
            if operation == "start":
                context = EpisodeContext.normalize(
                    session_id=request.get("session_id"),
                    turn_id=request.get("turn_id"),
                    task_id=request.get("task_id"),
                    platform="reference-jsonl",
                    model="deterministic-model",
                )
                response: dict[str, Any] = {
                    "episode_id": runner.start(context),
                    "profile": runner.runtime.effective_profile.value,
                }
            elif operation == "capabilities":
                response = {
                    "profile": runner.capabilities.maximum_profile().value,
                    "capabilities": {
                        name: getattr(runner.capabilities, name)
                        for name in runner.capabilities.__dataclass_fields__
                    },
                    "inventory": runner.tool_inventory(),
                }
            else:
                if context is None:
                    raise ValueError("start must be the first stateful operation")
                invocation = invocation or ToolInvocation.normalize(
                    context,
                    "deploy",
                    {"artifact": "app:fixture", "environment": "production"},
                )
                if operation == "health":
                    decision = runner.observe_health(str(request.get("value", "")))
                    response = {"outcome": decision.outcome, "reason_code": decision.reason_code}
                elif operation == "approve":
                    receipt = runner.approve_deployment(
                        invocation, approved=bool(request.get("approved", True))
                    )
                    response = {
                        "outcome": "approved" if receipt else "block",
                        "reason_code": "APPROVAL_RECORDED" if receipt else "APPROVAL_DENIED",
                    }
                elif operation == "deploy":
                    authorization = runner.authorize(invocation)
                    dispatched = (
                        runner.dispatch(invocation, authorization.permit)
                        if authorization.permit
                        else None
                    )
                    response = {
                        "outcome": authorization.outcome,
                        "reason_code": authorization.reason_code,
                        "executed": bool(dispatched and dispatched.executed),
                    }
                elif operation == "deliver":
                    chunks = request.get("chunks", [])
                    if not isinstance(chunks, list):
                        raise ValueError("chunks must be an array")
                    accepted = bool(request.get("accepted", True))
                    delivered = runner.deliver_output(
                        (str(chunk) for chunk in chunks), lint=_constant_lint(accepted)
                    )
                    response = {
                        "accepted": delivered.result.accepted,
                        "reason_code": delivered.result.reason_code,
                        "content": delivered.deliveries[0].decode("utf-8"),
                    }
                else:
                    raise ValueError(f"unknown operation: {operation}")
            payload = {"schema_version": 1, "ok": True, "result": response}
        except Exception as exc:
            payload = {
                "schema_version": 1,
                "ok": False,
                "error": {
                    "reason_code": "INVALID_REQUEST",
                    "line": line_number,
                    "detail": str(exc),
                },
            }
        destination.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        destination.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=Path(".belief-ledger-reference"))
    args = parser.parse_args()
    return serve_jsonl(sys.stdin, sys.stdout, state_root=args.state_root)


if __name__ == "__main__":
    raise SystemExit(main())
