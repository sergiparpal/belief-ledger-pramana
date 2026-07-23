"""Standalone policy validation and conservative scaffolding commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from belief_ledger_core.manifest import ToolDescriptor, ToolPolicyManifest

from .config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(prog="belief-ledger")
    commands = parser.add_subparsers(dest="command", required=True)
    policy = commands.add_parser("policy")
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    for name in ("validate", "inventory"):
        item = policy_commands.add_parser(name)
        item.add_argument("--format", choices=("human", "json"), default="human")
    for name in ("scaffold", "explain"):
        item = policy_commands.add_parser(name)
        item.add_argument("tool_name")
        item.add_argument("--format", choices=("human", "json"), default="human")
    args = parser.parse_args()
    snapshot, _ = load_config(initialize=False)
    from .runtime import _action_policy_data

    manifest = ToolPolicyManifest.load(
        _action_policy_data(snapshot.data),
        mode=snapshot.mode,
    )
    if args.policy_command == "validate":
        result: dict[str, Any] = {
            "schema_version": 1,
            "valid": True,
            "source_schema_version": manifest.source_schema_version,
            "normalized_schema_version": manifest.schema_version,
            "rules": len(manifest.rules),
        }
    elif args.policy_command == "inventory":
        result = {
            "schema_version": 1,
            "complete": False,
            "reason_code": "HOST_INVENTORY_NOT_CONNECTED",
            "items": [],
        }
    else:
        descriptor = ToolDescriptor.create(args.tool_name, {})
        if args.policy_command == "scaffold":
            result = {
                "schema_version": 1,
                "review_required": True,
                "active": False,
                "rule": manifest.scaffold(descriptor),
            }
        else:
            rule = manifest.match(descriptor.name)
            result = {
                "schema_version": 1,
                "matched": rule is not None,
                "tool": descriptor.name,
                "policy_id": rule.id if rule else None,
                "reason_code": "POLICY_MATCHED" if rule else "NO_POLICY",
            }
    if args.format == "json":
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
