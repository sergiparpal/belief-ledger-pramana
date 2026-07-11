"""Conservative Hermes tool provenance adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..models import Integrity, SourceKind
from .provenance import provenance_root


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    kind: SourceKind
    integrity: Integrity
    name: str
    root: str
    competence: dict[str, float] = field(default_factory=lambda: {"general": 0.5})


@dataclass(frozen=True, slots=True)
class AdaptedToolResult:
    adapter: str
    wrapper_source: SourceDescriptor
    wrapper_content: str
    successful: bool
    parsed: bool
    content_source: SourceDescriptor | None
    content_assertive: bool
    metadata: dict[str, Any]


class ToolAdapterRegistry:
    def adapt(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        *,
        status: str = "",
        tool_call_id: str = "",
    ) -> AdaptedToolResult:
        lowered = tool_name.casefold()
        family = _family(lowered)
        successful = _successful(status, result)
        parsed = _looks_structured(result)
        wrapper = SourceDescriptor(
            SourceKind.TOOL,
            Integrity.TRUSTED,
            tool_name,
            provenance_root(SourceKind.TOOL, identity=f"hermes:{tool_name}"),
            {"general": 0.85, "runtime_state": 0.95},
        )
        call_label = tool_call_id or "an uncorrelated call"
        wrapper_content = f"Hermes observed {tool_name} return {status or ('success' if successful else 'failure')} for {call_label}"
        content_source: SourceDescriptor | None = None
        metadata: dict[str, Any] = {"adapter": family, "tool_name": tool_name}

        if family == "web":
            url = str(args.get("url") or args.get("query_url") or _extract_url(result) or "")
            if url:
                host = urlsplit(url).hostname or url
                content_source = SourceDescriptor(
                    SourceKind.WEB,
                    Integrity.UNTRUSTED,
                    host,
                    provenance_root(SourceKind.WEB, identity=url),
                    {"general": 0.5},
                )
                metadata["url"] = url
        elif family == "file":
            path = str(args.get("path") or args.get("file_path") or "unknown")
            content_source = SourceDescriptor(
                SourceKind.DOCUMENT,
                Integrity.SEMI,
                path,
                provenance_root(SourceKind.DOCUMENT, identity=path, origin=path, content=result),
                {"general": 0.75, "library_internals": 0.85},
            )
            metadata["path"] = path
        elif family == "memory":
            identity = str(
                args.get("source_root")
                or args.get("memory_id")
                or args.get("collection")
                or "prior-ledger"
            )
            content_source = SourceDescriptor(
                SourceKind.LEDGER,
                Integrity.SEMI,
                "prior ledger",
                provenance_root(SourceKind.LEDGER, identity=identity, origin=identity),
                {"general": 0.5},
            )
            metadata.update(
                {
                    "transport_only": True,
                    "prior_source_root": str(args.get("source_root") or ""),
                }
            )
        elif family == "retrieval":
            identity = str(args.get("index") or args.get("collection") or tool_name)
            content_source = SourceDescriptor(
                SourceKind.RETRIEVER,
                Integrity.SEMI,
                identity,
                provenance_root(SourceKind.RETRIEVER, identity=identity),
                {"general": 0.65},
            )
        elif family == "delegation":
            identity = str(args.get("agent_id") or args.get("role") or tool_name)
            content_source = SourceDescriptor(
                SourceKind.MODEL,
                Integrity.SEMI,
                identity,
                provenance_root(SourceKind.MODEL, identity=identity),
                {"general": 0.55},
            )
        elif family == "plugin":
            content_source = SourceDescriptor(
                SourceKind.MODEL,
                Integrity.SEMI,
                tool_name,
                provenance_root(SourceKind.MODEL, identity=tool_name),
                {"general": 0.65},
            )
        elif family == "execution" and _direct_observable_command(args):
            content_source = wrapper
            metadata["content_pramana"] = "pratyaksha"
        # Shell execution is an observation about its concrete environment. Its
        # free-form stdout is not testimony until structured extraction supplies
        # an explicit domain source.
        return AdaptedToolResult(
            adapter=family,
            wrapper_source=wrapper,
            wrapper_content=wrapper_content,
            successful=successful,
            parsed=parsed,
            content_source=content_source,
            content_assertive=family
            in {"web", "file", "memory", "retrieval", "delegation", "plugin"}
            or content_source is wrapper,
            metadata=metadata,
        )


def _family(name: str) -> str:
    if name.startswith("pramana_"):
        return "plugin"
    if any(token in name for token in ("browser", "web", "http", "fetch", "search_web")):
        return "web"
    if any(
        token in name
        for token in ("read_file", "file_read", "list_file", "glob", "ripgrep", "grep")
    ):
        return "file"
    if "memory" in name:
        return "memory"
    if any(token in name for token in ("retrieve", "document", "rag", "search_index")):
        return "retrieval"
    if any(token in name for token in ("delegate", "subagent", "spawn_agent")):
        return "delegation"
    if any(token in name for token in ("shell", "terminal", "exec", "python", "command")):
        return "execution"
    return "unknown"


def _successful(status: str, result: str) -> bool:
    if status:
        return status.casefold() in {"ok", "success", "succeeded", "completed"}
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict):
            return not bool(parsed.get("error")) and parsed.get("ok", True) is not False
    except (TypeError, ValueError):
        pass
    lowered = result.casefold()
    return not lowered.startswith("error") and '"error"' not in lowered[:200]


def _looks_structured(result: str) -> bool:
    try:
        json.loads(result)
        return True
    except (TypeError, ValueError):
        return bool(result.strip())


def _extract_url(result: str) -> str | None:
    match = re.search(r"https?://[^\s\]\[<>{}\"']+", result)
    return match.group(0) if match else None


def _direct_observable_command(args: dict[str, Any]) -> bool:
    command = str(args.get("command") or args.get("cmd") or "").strip().casefold()
    return command.startswith(
        (
            "ls",
            "stat",
            "pwd",
            "git status",
            "git rev-parse",
            "pip index versions",
            "python --version",
            "python3 --version",
        )
    )
