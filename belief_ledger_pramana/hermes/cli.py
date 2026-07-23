"""Offline operator CLI for diagnostics, replay, export, and evaluations."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import yaml
from belief_ledger_core.manifest import ToolDescriptor, ToolPolicyManifest

from ..atomic import write_private_text_atomically
from ..compatibility import competing_transformers, transformer_has_precedence
from ..config import (
    ConfigError,
    load_config,
    packaged_yaml,
    require_private_path,
    validate_config,
)
from ..events import canonical_json, to_primitive, utc_now
from ..runtime import PluginRuntime


def setup_cli(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="belief_ledger_command", required=True)
    sub.add_parser("doctor", help="Run offline compatibility and integrity diagnostics")

    config = sub.add_parser("config", help="Inspect or initialize configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("show")
    config_sub.add_parser("path")
    config_sub.add_parser("validate")
    config_sub.add_parser("init")

    db = sub.add_parser("db", help="Inspect the event database")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("status")
    migrate = db_sub.add_parser("migrate")
    migrate.add_argument("--dry-run", action="store_true")
    db_sub.add_parser("verify-chain")
    db_sub.add_parser("replay")

    episode = sub.add_parser("episode", help="Inspect or export episodes")
    episode_sub = episode.add_subparsers(dest="episode_command", required=True)
    list_parser = episode_sub.add_parser("list")
    list_parser.add_argument("--limit", type=int, default=100)
    show = episode_sub.add_parser("show")
    show.add_argument("episode_id")
    export = episode_sub.add_parser("export")
    export.add_argument("episode_id")
    export.add_argument("--format", choices=("jsonl", "markdown"), default="jsonl")

    purge = sub.add_parser("purge", help="Irreversibly purge one episode")
    purge.add_argument("--episode", required=True)
    purge.add_argument("--confirm", required=True)

    evaluate = sub.add_parser("evaluate", help="Run deterministic evaluation suites")
    evaluate.add_argument("--suite", choices=("a", "b", "c", "d", "e", "all"), default="all")
    evaluate.add_argument("--offline", action="store_true", required=True)

    policy = sub.add_parser("policy", help="Inventory and validate tool policy coverage")
    policy_sub = policy.add_subparsers(dest="policy_command", required=True)
    for name in ("inventory", "validate"):
        command = policy_sub.add_parser(name)
        command.add_argument("--format", choices=("human", "json"), default="human")
    for name in ("scaffold", "explain"):
        command = policy_sub.add_parser(name)
        command.add_argument("tool_name")
        command.add_argument("--format", choices=("human", "json"), default="human")


def build_cli_handler(runtime: PluginRuntime) -> Any:
    def handler(args: argparse.Namespace) -> None:
        exit_code, output = run_cli(runtime, args)
        print(output)
        if exit_code:
            raise SystemExit(exit_code)

    return handler


def run_cli(runtime: PluginRuntime, args: argparse.Namespace) -> tuple[int, str]:
    command = args.belief_ledger_command
    try:
        if command == "doctor":
            report = doctor(runtime)
            return (0 if report["status"] in {"healthy", "degraded"} else 1, _json(report))
        if command == "config":
            snapshot, paths = load_config(hermes_home=runtime.hermes_home, initialize=True)
            if args.config_command == "path":
                return 0, str(snapshot.source or paths.config)
            if args.config_command == "show":
                return 0, canonical_json(snapshot.data)
            if args.config_command == "validate":
                validate_config(snapshot.data)
                return 0, _json(
                    {"ok": True, "digest": snapshot.digest, "warnings": snapshot.warnings}
                )
            return 0, _json({"ok": True, "path": str(paths.config), "created_or_present": True})
        if command == "policy":
            return _policy_command(runtime, args)
        if command == "db" and args.db_command == "migrate" and args.dry_run:
            snapshot, paths = load_config(hermes_home=runtime.hermes_home, initialize=False)
            database = paths.database
            current = 0
            if database.is_file():
                connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                try:
                    row = connection.execute(
                        "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
                    ).fetchone()
                    current = int(row[0]) if row else 0
                finally:
                    connection.close()
            return 0, _json(
                {
                    "dry_run": True,
                    "database": str(database),
                    "config_digest": snapshot.digest,
                    "current_schema": current,
                    "target_schema": 6,
                    "migration_required": current < 6,
                    "backup_required": database.is_file() and current < 6,
                    "writes_performed": False,
                }
            )
        runtime.ensure_initialized()
        assert runtime.store is not None
        if command == "db":
            if args.db_command == "status":
                return 0, _json(
                    {
                        "database": str(runtime.store.database),
                        "schema": runtime.store.migration.to_version,
                        "fts5": runtime.store.migration.fts5_available,
                        "episodes": len(runtime.store.list_episodes(limit=1_000)),
                        "events": len(runtime.store.events()),
                    }
                )
            if args.db_command == "verify-chain":
                ok, digest = runtime.store.verify_hash_chain()
                return 0, _json({"ok": ok, "heads": digest})
            if args.db_command == "replay":
                return 0, _json(to_primitive(runtime.store.replay()))
            return 0, _json(to_primitive(runtime.store.migration))
        if command == "episode":
            if args.episode_command == "list":
                return 0, _json(
                    [
                        {
                            "id": episode.id,
                            "session_id": episode.session_id,
                            "task_id": episode.task_id,
                            "stakes": episode.default_stakes.value,
                            "turn": episode.current_turn,
                            "state": episode.state,
                        }
                        for episode in runtime.store.list_episodes(args.limit)
                    ]
                )
            episode = runtime.store.get_episode(args.episode_id)
            if episode is None:
                return 2, _json({"ok": False, "error": "episode_not_found"})
            if args.episode_command == "show":
                return 0, _json(
                    {
                        "episode": to_primitive(episode),
                        "beliefs": [
                            to_primitive(item)
                            for item in runtime.store.list_beliefs(episode.id, limit=500)
                        ],
                        "conflicts": [
                            to_primitive(item)
                            for item in runtime.store.list_conflicts(episode.id, state=None)
                        ],
                        "retractions": [
                            to_primitive(item)
                            for item in runtime.store.list_retractions(episode.id, state=None)
                        ],
                    }
                )
            return 0, str(export_episode(runtime, episode.id, args.format))
        if command == "purge":
            if args.episode != args.confirm:
                return 2, _json({"ok": False, "error": "confirmation_mismatch"})
            result = runtime.store.purge_episode(args.episode, confirmation=args.confirm)
            return 0, _json({"ok": True, "result": to_primitive(result)})
        if command == "evaluate":
            from evaluations.report import run_offline_evaluations

            report_path = run_offline_evaluations(
                suite=args.suite,
                output_dir=(runtime.paths.exports if runtime.paths else Path.cwd()),
            )
            return 0, str(report_path)
        return 2, _json({"ok": False, "error": "unknown_command"})
    except Exception as exc:
        return 1, _json({"ok": False, "error": type(exc).__name__, "message": str(exc)[:1_000]})


def _policy_command(runtime: PluginRuntime, args: argparse.Namespace) -> tuple[int, str]:
    manifest = ToolPolicyManifest.load(packaged_yaml("action-policies.yaml"), mode="enforce")
    descriptors = _tool_descriptors(runtime)
    if args.policy_command == "validate":
        payload: Any = {
            "schema_version": 1,
            "valid": True,
            "source_schema_version": manifest.source_schema_version,
            "normalized_schema_version": manifest.schema_version,
            "rules": len(manifest.rules),
        }
    elif args.policy_command == "inventory":
        items = manifest.classify_inventory(descriptors, complete=False)
        payload = {
            "schema_version": 1,
            "complete": False,
            "capability": "tool_inventory",
            "reason_code": "HERMES_COMPLETE_INVENTORY_UNPROVEN",
            "items": [
                {
                    "name": item.descriptor.name,
                    "namespace": item.descriptor.namespace,
                    "schema_digest": item.descriptor.schema_digest,
                    "status": item.status.value,
                    "policy_id": item.policy_id,
                    "reason_code": item.reason_code,
                }
                for item in items
            ],
        }
    else:
        descriptor = next(
            (item for item in descriptors if item.name == args.tool_name),
            ToolDescriptor.create(args.tool_name, {}),
        )
        if args.policy_command == "scaffold":
            payload = {
                "schema_version": 1,
                "active": False,
                "review_required": True,
                "rule": manifest.scaffold(descriptor),
            }
        else:
            rule = manifest.match(descriptor.name, descriptor.namespace)
            payload = {
                "schema_version": 1,
                "tool": descriptor.name,
                "namespace": descriptor.namespace,
                "schema_digest": descriptor.schema_digest,
                "matched": rule is not None,
                "policy": (
                    {
                        "id": rule.id,
                        "revision": rule.revision,
                        "effectful": rule.effectful,
                        "base_stakes": rule.base_stakes,
                        "target_fields": list(rule.target_fields),
                        "preconditions": list(rule.preconditions),
                    }
                    if rule
                    else None
                ),
                "reason_code": "POLICY_MATCHED" if rule else "NO_POLICY",
            }
    rendered = _json(payload)
    if args.format == "json":
        return 0, rendered
    if args.policy_command == "validate":
        return (
            0,
            f"valid v{payload['normalized_schema_version']} manifest: {payload['rules']} rules",
        )
    return 0, rendered


def _tool_descriptors(runtime: PluginRuntime) -> tuple[ToolDescriptor, ...]:
    registered = getattr(runtime.ctx, "tools", {})
    if not isinstance(registered, dict):
        return ()
    descriptors = []
    for name, detail in registered.items():
        value = detail if isinstance(detail, dict) else {}
        schema = value.get("schema", {})
        descriptors.append(
            ToolDescriptor.create(
                str(name),
                schema if isinstance(schema, dict) else {},
                namespace=str(value.get("toolset", "")),
                description=str(value.get("description", "")),
            )
        )
    return tuple(descriptors)


def doctor(runtime: PluginRuntime) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    errors: list[str] = list(runtime.compatibility.errors)
    warnings: list[str] = list(runtime.compatibility.warnings)
    checks["python"] = runtime.compatibility.python_version
    checks["hermes_version"] = runtime.compatibility.hermes_version
    checks["compatibility_mode"] = runtime.compatibility.mode.value
    checks["capabilities"] = runtime.compatibility.capabilities
    checks["host_capabilities"] = {
        name: getattr(runtime.host_capabilities, name)
        for name in runtime.host_capabilities.__dataclass_fields__
    }
    checks["safe_mode"] = os.environ.get("HERMES_SAFE_MODE") == "1"
    if checks["safe_mode"]:
        errors.append("HERMES_SAFE_MODE=1 skips plugin discovery")
    try:
        runtime.ensure_initialized()
        assert runtime.store is not None and runtime.paths is not None
        checks["config"] = {
            "source": str(runtime.config.source) if runtime.config.source else "packaged",
            "digest": runtime.config.digest,
            "warnings": runtime.config.warnings,
        }
        assert runtime.profile_selection is not None
        checks["enforcement_profile"] = {
            "requested": runtime.profile_selection.requested.value,
            "effective": runtime.profile_selection.effective.value,
            "missing": list(runtime.profile_selection.missing),
            "reason_codes": list(runtime.profile_selection.reason_codes),
            "downgraded": runtime.profile_selection.downgraded,
        }
        ok, heads = runtime.store.verify_hash_chain()
        checks["database"] = {
            "path": str(runtime.store.database),
            "hash_chain": ok,
            "heads": heads,
            "schema": runtime.store.migration.to_version,
            "fts5": runtime.store.migration.fts5_available,
        }
        permission_issues = _permission_issues(runtime)
        checks["permissions"] = {"ok": not permission_issues, "issues": permission_issues}
        warnings.extend(permission_issues)
        runtime.paths.locks.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, probe_name = tempfile.mkstemp(prefix="doctor-", dir=runtime.paths.locks)
        os.close(fd)
        Path(probe_name).unlink()
        checks["state_write_test"] = True
    except Exception as exc:
        errors.append(f"state diagnostics failed: {type(exc).__name__}: {exc}")

    own_transform = getattr(runtime, "transform_callback", None)
    competitors = competing_transformers(runtime.ctx, own_transform)
    checks["competing_transformers"] = competitors
    checks["transform_precedence"] = (
        transformer_has_precedence(runtime.ctx, own_transform) if own_transform else None
    )
    if competitors and checks["transform_precedence"] is False:
        errors.append("belief-ledger transform lacks effective precedence")
    manager = getattr(runtime.ctx, "_manager", None)
    tool_names = sorted(getattr(manager, "_plugin_tool_names", ())) if manager else []
    checks["registered_tools"] = tool_names
    required_tools = {
        "pramana_record_inference",
        "pramana_query",
        "pramana_explain",
        "pramana_request_verification",
    }
    missing_tools = sorted(required_tools - set(tool_names)) if manager else []
    if missing_tools:
        errors.append("missing registered tools: " + ", ".join(missing_tools))
    manifest = getattr(runtime.ctx, "manifest", None)
    manifest_name = str(getattr(manifest, "name", "belief-ledger-pramana"))
    manifest_key = str(getattr(manifest, "key", "") or manifest_name)
    host_config_path = (
        runtime.paths.hermes_home / "config.yaml"
        if runtime.paths is not None
        else Path(runtime.hermes_home or Path.home() / ".hermes") / "config.yaml"
    )
    host_plugins: dict[str, Any] = {}
    if host_config_path.is_file():
        try:
            host_data = yaml.safe_load(host_config_path.read_text(encoding="utf-8")) or {}
            if isinstance(host_data, dict) and isinstance(host_data.get("plugins"), dict):
                host_plugins = host_data["plugins"]
        except (OSError, yaml.YAMLError) as exc:
            warnings.append(f"could not inspect Hermes plugin activation config: {exc}")
    enabled = {str(item) for item in host_plugins.get("enabled", ()) if isinstance(item, str)}
    disabled = {str(item) for item in host_plugins.get("disabled", ()) if isinstance(item, str)}
    loaded_entry = None
    if manager is not None:
        loaded_entry = getattr(manager, "_plugins", {}).get(manifest_key)
    loaded_by_manager = bool(getattr(loaded_entry, "enabled", False))
    explicitly_enabled = bool({manifest_key, manifest_name} & enabled)
    explicitly_disabled = bool({manifest_key, manifest_name} & disabled)
    checks["activation"] = {
        "config_path": str(host_config_path),
        "enabled_entries": sorted(enabled),
        "disabled_entries": sorted(disabled),
        "explicitly_enabled": explicitly_enabled,
        "explicitly_disabled": explicitly_disabled,
        "manager_loaded": loaded_by_manager,
    }
    if explicitly_disabled:
        errors.append("plugin appears in Hermes plugins.disabled")
    checks["loaded_module"] = runtime.loaded_module_path or __name__
    checks["manifest_source"] = runtime.manifest_source or str(
        getattr(manifest, "source", "unknown")
    )
    checks["health_reasons"] = runtime.health_reasons
    if runtime.health_reasons:
        warnings.extend(runtime.health_reasons)
    status = "unavailable" if errors else "degraded" if warnings else "healthy"
    return {
        "status": status,
        "full_conformance": status == "healthy" and runtime.compatibility.full_conformance,
        "strict_enforcement": bool(
            runtime.profile_selection
            and runtime.profile_selection.effective.value == "strict"
            and status == "healthy"
        ),
        "checks": checks,
        "warnings": sorted(set(warnings)),
        "errors": sorted(set(errors)),
    }


def export_episode(runtime: PluginRuntime, episode_id: str, export_format: str) -> Path:
    runtime.ensure_initialized()
    assert runtime.store is not None and runtime.paths is not None
    episode = runtime.store.get_episode(episode_id)
    if episode is None:
        raise ValueError("episode does not exist")
    runtime.paths.exports.mkdir(mode=0o700, parents=True, exist_ok=True)
    suffix = "jsonl" if export_format == "jsonl" else "md"
    target = runtime.paths.exports / f"{episode_id}.{suffix}"
    if export_format == "jsonl":
        text = "\n".join(canonical_json(event) for event in runtime.store.events(episode_id)) + "\n"
    elif export_format == "markdown":
        lines = [
            f"# Belief ledger episode {episode_id}",
            "",
            f"Exported: {utc_now().isoformat()}",
            "",
        ]
        for belief in runtime.store.list_beliefs(episode_id):
            lines.append(
                f"- [{belief.id}] `{belief.status.value}` `{belief.pramana.value}` — {belief.content}"
            )
        text = "\n".join(lines) + "\n"
    else:
        raise ValueError("export format must be jsonl or markdown")
    write_private_text_atomically(target, text)
    return target


def _permission_issues(runtime: PluginRuntime) -> list[str]:
    assert runtime.paths is not None and runtime.store is not None
    issues: list[str] = []
    for directory in (
        runtime.paths.root,
        runtime.paths.evidence,
        runtime.paths.exports,
        runtime.paths.locks,
    ):
        if directory.exists():
            try:
                require_private_path(directory, "state directory", directory=True)
            except ConfigError as exc:
                issues.append(str(exc))
    for file_path in (runtime.store.database, runtime.paths.integrity_key, runtime.config.source):
        if file_path and Path(file_path).exists():
            try:
                require_private_path(Path(file_path), "state file")
            except ConfigError as exc:
                issues.append(str(exc))
    return issues


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
