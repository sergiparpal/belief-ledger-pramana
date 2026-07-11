"""In-session `/ledger` command without private CLI references."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable

from ..models import Stakes
from ..runtime import PluginRuntime
from .cli import export_episode


def build_ledger_command(runtime: PluginRuntime) -> Callable[[str], str]:
    def ledger(raw_args: str) -> str:
        try:
            parts = shlex.split(raw_args or "")
        except ValueError as exc:
            return f"ledger: invalid arguments: {exc}"
        command = parts[0].casefold() if parts else "help"
        try:
            service = runtime.current_service()
            if command == "status":
                beliefs = service.store.list_beliefs(service.episode_id)
                counts: dict[str, int] = {}
                for belief in beliefs:
                    counts[belief.status.value] = counts.get(belief.status.value, 0) + 1
                return json.dumps(
                    {
                        "episode_id": service.episode_id,
                        "stakes": service.episode.default_stakes.value,
                        "health": runtime.health.value,
                        "beliefs": counts,
                        "open_conflicts": len(service.store.list_conflicts(service.episode_id)),
                        "active_retractions": len(
                            service.store.list_retractions(service.episode_id)
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            if command == "conflicts":
                return json.dumps(
                    [
                        {
                            "id": item.id,
                            "left": item.left_belief_id,
                            "right": item.right_belief_id,
                            "verification_task": item.verification_task_id,
                        }
                        for item in service.store.list_conflicts(service.episode_id)
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            if command == "retractions":
                return json.dumps(
                    [
                        {
                            "id": item.id,
                            "defeated": item.defeated_belief_id,
                            "cause": item.cause,
                            "descendants": list(item.descendants),
                        }
                        for item in service.store.list_retractions(service.episode_id)
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            if command == "belief" and len(parts) == 2:
                return json.dumps(service.explain(parts[1]), ensure_ascii=False, sort_keys=True)
            if command == "stakes" and len(parts) == 2:
                stakes = Stakes(parts[1].casefold())
                event_ids = service.set_stakes(stakes, user_initiated=True)
                return json.dumps({"stakes": stakes.value, "event_ids": event_ids})
            if command == "export":
                export_format = parts[1].casefold() if len(parts) > 1 else "jsonl"
                if export_format not in {"jsonl", "markdown"}:
                    return "ledger: export format must be jsonl or markdown"
                path = export_episode(runtime, service.episode_id, export_format)
                return f"Ledger export written to {path}"
            if command != "help":
                return "ledger: unknown or incomplete subcommand; use /ledger help"
            return (
                "/ledger status | conflicts | retractions | belief <id> | "
                "stakes <low|med|high|critical> | export [jsonl|markdown] | help"
            )
        except Exception as exc:
            return f"ledger: {type(exc).__name__}: {str(exc)[:300]}"

    return ledger
