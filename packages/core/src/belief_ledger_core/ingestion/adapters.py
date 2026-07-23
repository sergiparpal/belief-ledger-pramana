"""Conservative host tool provenance adapters."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

from ..models import Integrity, SourceKind
from .provenance import provenance_root
from .tool import redact_secrets


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
    observations: tuple[str, ...] = ()


class ToolAdapterRegistry:
    def __init__(self, host_name: str = "host") -> None:
        self.host_name = host_name.strip().casefold() or "host"

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
            provenance_root(SourceKind.TOOL, identity=f"{self.host_name}:{tool_name}"),
            {"general": 0.85, "runtime_state": 0.95},
        )
        call_label = tool_call_id or "an uncorrelated call"
        wrapper_content = (
            f"{self.host_name.title()} observed {tool_name} return "
            f"{status or ('success' if successful else 'failure')} for {call_label}"
        )
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
                metadata["url"] = redact_secrets(url)[0]
        elif family == "file":
            path = str(args.get("path") or args.get("file_path") or "unidentified-file")
            safe_path = redact_secrets(path)[0]
            content_source = SourceDescriptor(
                SourceKind.DOCUMENT,
                Integrity.SEMI,
                safe_path,
                provenance_root(
                    SourceKind.DOCUMENT,
                    identity=safe_path,
                    origin=safe_path,
                    content=result,
                ),
                {"general": 0.75, "library_internals": 0.85},
            )
            metadata["path"] = safe_path
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
                    "prior_source_root": redact_secrets(str(args.get("source_root") or ""))[0],
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
        # A command runner is a trustworthy witness only for the fact that it
        # ran and returned a status.  Its stdout can be attacker-controlled,
        # shell-dependent, or describe an unrelated domain, so it is never
        # promoted as direct perception without a typed source adapter.
        return AdaptedToolResult(
            adapter=family,
            wrapper_source=wrapper,
            wrapper_content=wrapper_content,
            successful=successful,
            parsed=parsed,
            content_source=content_source,
            content_assertive=family
            in {"web", "file", "memory", "retrieval", "delegation", "plugin"},
            metadata=metadata,
            observations=_typed_observations(lowered, args, result, successful),
        )


def _family(name: str) -> str:
    if name.startswith("pramana_"):
        return "plugin"
    if any(token in name for token in ("browser", "web", "http", "fetch", "search_web")):
        return "web"
    if any(
        token in name
        for token in (
            "read_file",
            "file_read",
            "list_file",
            "list_directory",
            "list_dir",
            "stat_file",
            "file_stat",
            "glob",
            "ripgrep",
            "grep",
        )
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


def _typed_observations(
    tool_name: str, args: dict[str, Any], result: str, successful: bool
) -> tuple[str, ...]:
    """Derive only target-bound facts from recognised structured tool APIs.

    Shell output and free-form text remain untrusted descriptions.  These
    propositions are emitted only when a known observational API returns a
    JSON object that binds its result back to the requested path or host.
    """

    if not successful:
        return ()
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return ()
    if not isinstance(payload, dict):
        return ()

    path = _requested_path(args)
    returned_path = str(payload.get("path") or payload.get("directory") or "")
    safe_path = redact_secrets(path)[0]
    if tool_name in {"stat_file", "file_stat", "stat_path"}:
        if path and payload.get("exists") is True and _same_path(path, returned_path):
            return (f"Target {safe_path} exists",)
        return ()
    if tool_name in {"list_directory", "list_dir", "list_files"}:
        entries = payload.get("entries")
        if path and isinstance(entries, list) and _same_path(path, returned_path):
            observed_directory = redact_secrets(str(PurePath(path)))[0]
            return (f"Parent {observed_directory} exists",)
        return ()
    if tool_name in {"environment_identity", "get_environment", "system_info", "get_system_info"}:
        identity = (
            payload.get("environment_id") or payload.get("environment") or payload.get("hostname")
        )
        if isinstance(identity, str) and identity.strip():
            return ("The current execution environment is identified",)
    return ()


def _requested_path(args: dict[str, Any]) -> str:
    return str(args.get("path") or args.get("file_path") or args.get("directory") or "").strip()


def _same_path(requested: str, returned: str) -> bool:
    def normalize(value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        normalized = normalized.rstrip("/")
        return normalized.casefold() if os.name == "nt" else normalized

    return bool(returned.strip()) and normalize(requested) == normalize(returned)
