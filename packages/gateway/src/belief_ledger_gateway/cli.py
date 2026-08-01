"""Host-neutral Belief Ledger command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from belief_ledger_core import (
    BeliefLedger,
    EpisodeContext,
    ToolDescriptor,
    ToolInvocation,
    ToolPolicyManifest,
)
from belief_ledger_core.events import to_primitive
from belief_ledger_core.ingestion.tool import redact_secrets

from .protocol import open_gateway_ledger, serve_jsonl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="belief-ledger")
    parser.add_argument("--state-root", type=Path, default=Path(".belief-ledger"))
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="initialize a private local state root")
    _format(initialize)
    demo = commands.add_parser("demo", help="run an offline host-neutral decision")
    _format(demo)

    policy = commands.add_parser("policy")
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    for name in ("validate", "inventory"):
        _format(policy_commands.add_parser(name))
    for name in ("scaffold", "explain"):
        item = policy_commands.add_parser(name)
        item.add_argument("tool_name")
        item.add_argument("--namespace", default="")
        _format(item)

    ledger = commands.add_parser("ledger")
    ledger_commands = ledger.add_subparsers(dest="ledger_command", required=True)
    for name in ("status", "verify-chain", "replay"):
        _format(ledger_commands.add_parser(name))

    episode = commands.add_parser("episode")
    episode_commands = episode.add_subparsers(dest="episode_command", required=True)
    listed = episode_commands.add_parser("list")
    listed.add_argument("--limit", type=int, default=100)
    _format(listed)
    for name in ("show", "export"):
        item = episode_commands.add_parser(name)
        item.add_argument("episode_id")
        _format(item)

    serve = commands.add_parser("serve")
    serve.add_argument("--transport", choices=("jsonl",), default="jsonl")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return serve_jsonl(sys.stdin, sys.stdout, state_root=args.state_root)
    try:
        result = _run(args)
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", "COMMAND_FAILED")
        detail = (
            redact_secrets(str(exc))[0]
            if hasattr(exc, "reason_code")
            else "command could not be completed"
        )
        result = {
            "schema_version": 1,
            "ok": False,
            "reason_code": reason_code,
            "detail": detail,
        }
        _emit(result, getattr(args, "format", "human"))
        return 1
    _emit(result, args.format)
    return 0


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "init":
        root = args.state_root.expanduser().resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        config_path = root / "config.yaml"
        if not config_path.exists():
            config_path.write_text("schema_version: 1\nmode: enforce\n", encoding="utf-8")
            config_path.chmod(0o600)
        policy_path = root / "policies.json"
        if not policy_path.exists():
            policy_path.write_text(
                json.dumps(_core_default_manifest(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            policy_path.chmod(0o600)
        open_gateway_ledger(root)
        return {
            "schema_version": 1,
            "initialized": True,
            "state_root": str(root),
            "reason_code": "STATE_ROOT_READY",
        }
    if args.command == "demo":
        return _demo(args.state_root)
    if args.command == "policy":
        return _policy(args)
    ledger = open_gateway_ledger(args.state_root)
    if args.command == "ledger":
        if args.ledger_command == "status":
            return {
                "schema_version": 1,
                "state_root": str(ledger.state_root),
                "episodes": len(ledger.list_episodes()),
                "profile": ledger.effective_profile.value,
                "reason_code": "LEDGER_READY",
            }
        if args.ledger_command == "verify-chain":
            return cast(dict[str, Any], to_primitive(ledger.verify_chain()))
        replay = ledger.replay()
        return {
            **cast(dict[str, Any], to_primitive(replay)),
            "deterministic": replay.deterministic,
        }
    if args.episode_command == "list":
        return {
            "schema_version": 1,
            "episodes": [to_primitive(item) for item in ledger.list_episodes(limit=args.limit)],
        }
    if args.episode_command == "show":
        return {"schema_version": 1, "episode": to_primitive(ledger.episode(args.episode_id))}
    return {
        "schema_version": 1,
        "episode_id": args.episode_id,
        "events": list(ledger.export_episode(args.episode_id)),
    }


def _demo(state_root: Path) -> dict[str, Any]:
    manifest = {
        "schema_version": 2,
        "rules": [
            {
                "id": "customer-message",
                "revision": "demo-v1",
                "effectful": True,
                "base_stakes": "high",
                "exact": ["send_customer_message"],
                "namespace": "demo",
                "target_fields": ["recipient"],
                "preconditions": ["recipient_identity"],
                "approval_policy": "required",
                "minimum_source_integrity": "trusted",
                "canonicalization_version": 1,
            }
        ],
    }
    # A temporary state root keeps repeated demos deterministic and leaves no product state behind.
    parent = state_root.expanduser().resolve().parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="belief-ledger-demo-", dir=parent) as directory:
        ledger = BeliefLedger.open(state_root=Path(directory), manifest=manifest)
        context = EpisodeContext.normalize(
            session_id="offline-demo",
            turn_id="turn-1",
            task_id="message-review",
            platform="gateway",
            model="deterministic",
        )
        episode = ledger.start_episode(context)
        invocation = ToolInvocation.normalize(
            context,
            "send_customer_message",
            {"recipient": "customer-42", "body": "Your order shipped."},
            namespace="demo",
        )
        decision = ledger.evaluate_action(episode.id, invocation)
        return {
            "schema_version": 1,
            "product": "Belief Ledger",
            "profile": "observe",
            "decision": {
                "outcome": decision.outcome,
                "reason_code": decision.reason_code,
                "missing": list(decision.decision.missing),
                "executed": False,
            },
            "safe_next_step": "Obtain trusted recipient identity evidence and exact host approval.",
        }


def _policy(args: argparse.Namespace) -> dict[str, Any]:
    policy_path = args.state_root.expanduser().resolve() / "policies.json"
    manifest = (
        open_gateway_ledger(args.state_root).manifest
        if policy_path.is_file()
        else _default_manifest()
    )
    if args.policy_command == "validate":
        return {
            "schema_version": 1,
            "valid": True,
            "source_schema_version": manifest.source_schema_version,
            "normalized_schema_version": manifest.schema_version,
            "rules": len(manifest.rules),
        }
    if args.policy_command == "inventory":
        return {
            "schema_version": 1,
            "complete": False,
            "reason_code": "TOOL_INVENTORY_NOT_CONNECTED",
            "items": [],
        }
    descriptor = ToolDescriptor.create(args.tool_name, {}, namespace=args.namespace)
    if args.policy_command == "scaffold":
        return {
            "schema_version": 1,
            "review_required": True,
            "active": False,
            "rule": manifest.scaffold(descriptor),
        }
    rule = manifest.match(descriptor.name, descriptor.namespace)
    return {
        "schema_version": 1,
        "matched": rule is not None,
        "tool": descriptor.name,
        "namespace": descriptor.namespace,
        "policy_id": rule.id if rule else None,
        "policy": asdict(rule) if rule else None,
        "reason_code": "POLICY_MATCHED" if rule else "NO_POLICY",
    }


def _default_manifest() -> ToolPolicyManifest:
    value = json.loads(
        files("belief_ledger_core.data")
        .joinpath("tool-policies-v2.json")
        .read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise RuntimeError("packaged policy manifest is invalid")
    return ToolPolicyManifest.load(value)


def _core_default_manifest() -> dict[str, Any]:
    value = json.loads(
        files("belief_ledger_core.data")
        .joinpath("tool-policies-v2.json")
        .read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise RuntimeError("packaged gateway policy manifest is invalid")
    return value


def _format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("human", "json"), default="human")


def _emit(result: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
