"""Audited Hermes/Python capability checks without importing host internals eagerly."""

from __future__ import annotations

import importlib.metadata
import inspect
import platform
import sys
from dataclasses import dataclass
from typing import Any

from .models import CompatibilityMode

AUDITED_HERMES_VERSION = "0.18.2"
AUDITED_HERMES_COMMIT = "3b2ef789dfcf92f5b7b18c08c59d25948e50857f"
REQUIRED_HOOKS = {
    "pre_llm_call",
    "pre_tool_call",
    "transform_tool_result",
    "transform_llm_output",
    "post_llm_call",
    "pre_verify",
    "on_session_start",
    "on_session_end",
    "on_session_finalize",
    "on_session_reset",
    "subagent_start",
    "subagent_stop",
    "post_approval_response",
}


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    mode: CompatibilityMode
    hermes_version: str | None
    python_version: str
    capabilities: dict[str, bool]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def full_conformance(self) -> bool:
        return self.mode is CompatibilityMode.FULL and not self.errors


def inspect_host(ctx: Any) -> CompatibilityReport:
    errors: list[str] = []
    warnings: list[str] = []
    version = _distribution_version()
    python_ok = (3, 11) <= sys.version_info[:2] < (3, 14)
    if not python_ok:
        errors.append("Python must be >=3.11,<3.14")
    version_ok = version is not None and _supported_version(version)
    if not version_ok:
        errors.append(f"Hermes {version or 'unknown'} is outside audited >=0.18.2,<0.19 range")

    capabilities = {
        "register_tool": callable(getattr(ctx, "register_tool", None)),
        "register_hook": callable(getattr(ctx, "register_hook", None)),
        "register_middleware": callable(getattr(ctx, "register_middleware", None)),
        "register_command": callable(getattr(ctx, "register_command", None)),
        "register_cli_command": callable(getattr(ctx, "register_cli_command", None)),
        "llm_facade": inspect.getattr_static(ctx, "llm", None) is not None,
    }
    missing = sorted(name for name, available in capabilities.items() if not available)
    if missing:
        errors.append("missing Hermes capabilities: " + ", ".join(missing))

    hook_set, middleware_set = _host_contract_sets(ctx)
    if hook_set is not None:
        absent_hooks = sorted(REQUIRED_HOOKS - hook_set)
        if absent_hooks:
            errors.append("host lacks required hooks: " + ", ".join(absent_hooks))
    if middleware_set is not None and "llm_request" not in middleware_set:
        errors.append("host lacks llm_request middleware")

    if not python_ok or not capabilities["register_hook"] or not capabilities["register_tool"]:
        mode = CompatibilityMode.DIAGNOSTICS_ONLY
    elif (
        version_ok
        and capabilities["register_middleware"]
        and (middleware_set is None or "llm_request" in middleware_set)
    ):
        mode = CompatibilityMode.FULL
    elif capabilities["register_hook"]:
        mode = CompatibilityMode.HOOK_CONTEXT
        warnings.append(
            "per-request context injection is unavailable; compatibility context is per turn"
        )
    else:
        mode = CompatibilityMode.DIAGNOSTICS_ONLY
    if mode is not CompatibilityMode.FULL:
        warnings.append("strict enforcement is not claimed in this compatibility mode")
    return CompatibilityReport(
        mode,
        version,
        platform.python_version(),
        capabilities,
        tuple(errors),
        tuple(warnings),
    )


def competing_transformers(ctx: Any, own_callback: Any | None = None) -> tuple[str, ...]:
    manager = getattr(ctx, "_manager", None)
    hooks = getattr(manager, "_hooks", {}) if manager is not None else {}
    callbacks = hooks.get("transform_llm_output", []) if isinstance(hooks, dict) else []
    names = []
    for callback in callbacks:
        if own_callback is not None and callback is own_callback:
            continue
        names.append(getattr(callback, "__qualname__", repr(callback)))
    return tuple(names)


def transformer_has_precedence(ctx: Any, own_callback: Any) -> bool:
    manager = getattr(ctx, "_manager", None)
    hooks = getattr(manager, "_hooks", {}) if manager is not None else {}
    callbacks = hooks.get("transform_llm_output", []) if isinstance(hooks, dict) else []
    return not callbacks or callbacks[0] is own_callback


def _distribution_version() -> str | None:
    for name in ("hermes-agent", "hermes_agent"):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _supported_version(value: str) -> bool:
    numeric = value.split("+", 1)[0].split("-", 1)[0]
    parts = numeric.split(".")
    try:
        major, minor, patch = (int(parts[index]) for index in range(3))
    except (IndexError, ValueError):
        return False
    return (major, minor, patch) >= (0, 18, 2) and (major, minor) < (0, 19)


def _host_contract_sets(ctx: Any) -> tuple[set[str] | None, set[str] | None]:
    try:
        from hermes_cli.middleware import VALID_MIDDLEWARE  # type: ignore[import-untyped]
        from hermes_cli.plugins import VALID_HOOKS  # type: ignore[import-untyped]

        return set(VALID_HOOKS), set(VALID_MIDDLEWARE)
    except ImportError:
        manager = getattr(ctx, "_manager", None)
        hook_set = set(getattr(manager, "valid_hooks", ())) or None
        middleware_set = set(getattr(manager, "valid_middleware", ())) or None
        return hook_set, middleware_set
