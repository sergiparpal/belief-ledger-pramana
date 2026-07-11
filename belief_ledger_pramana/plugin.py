"""Hermes plugin registration entry point."""

from __future__ import annotations

import logging
from typing import Any

from .compatibility import inspect_host
from .hermes.cli import build_cli_handler, setup_cli
from .hermes.hooks import HermesHooks
from .hermes.middleware import LlmRequestMiddleware
from .hermes.schemas import (
    EXPLAIN_SCHEMA,
    QUERY_SCHEMA,
    RECORD_INFERENCE_SCHEMA,
    REQUEST_VERIFICATION_SCHEMA,
)
from .hermes.slash_commands import build_ledger_command
from .hermes.tools import build_tool_handlers
from .runtime import PluginRuntime

logger = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    """Register capabilities cheaply; all state/database work remains lazy."""

    if getattr(ctx, "_belief_ledger_pramana_registered", False):
        return
    compatibility = inspect_host(ctx)
    runtime = PluginRuntime(ctx, compatibility=compatibility)
    runtime.loaded_module_path = str(__file__)
    runtime.manifest_source = str(getattr(getattr(ctx, "manifest", None), "source", "") or "")
    hooks = HermesHooks(runtime)
    middleware = LlmRequestMiddleware(runtime)
    handlers = build_tool_handlers(runtime)

    schemas = {
        "pramana_record_inference": RECORD_INFERENCE_SCHEMA,
        "pramana_query": QUERY_SCHEMA,
        "pramana_explain": EXPLAIN_SCHEMA,
        "pramana_request_verification": REQUEST_VERIFICATION_SCHEMA,
    }
    descriptions = {
        "pramana_record_inference": "Record a validated derived belief",
        "pramana_query": "Query typed episode beliefs",
        "pramana_explain": "Explain a belief and its defeat trace",
        "pramana_request_verification": "Request bounded verification",
    }
    if callable(getattr(ctx, "register_tool", None)):
        for name in (
            "pramana_record_inference",
            "pramana_query",
            "pramana_explain",
            "pramana_request_verification",
        ):
            ctx.register_tool(
                name=name,
                toolset="belief_ledger_pramana",
                schema=schemas[name],
                handler=handlers[name],
                description=descriptions[name],
                emoji="📚",
            )

    hook_callbacks = {
        "pre_llm_call": hooks.pre_llm_call,
        "pre_tool_call": hooks.pre_tool_call,
        "transform_tool_result": hooks.transform_tool_result,
        "transform_llm_output": hooks.transform_llm_output,
        "post_llm_call": hooks.post_llm_call,
        "pre_verify": hooks.pre_verify,
        "on_session_start": hooks.on_session_start,
        "on_session_end": hooks.on_session_end,
        "on_session_finalize": hooks.on_session_finalize,
        "on_session_reset": hooks.on_session_reset,
        "subagent_start": hooks.subagent_start,
        "subagent_stop": hooks.subagent_stop,
        "post_approval_response": hooks.post_approval_response,
    }
    if callable(getattr(ctx, "register_hook", None)):
        for name, callback in hook_callbacks.items():
            ctx.register_hook(name, callback)
    runtime.transform_callback = hook_callbacks["transform_llm_output"]

    if callable(getattr(ctx, "register_middleware", None)):
        ctx.register_middleware("llm_request", middleware)
    if callable(getattr(ctx, "register_command", None)):
        ctx.register_command(
            "ledger",
            build_ledger_command(runtime),
            description="Inspect the current typed belief ledger",
            args_hint="status|conflicts|retractions|belief|stakes|export|help",
        )
    if callable(getattr(ctx, "register_cli_command", None)):
        ctx.register_cli_command(
            name="belief-ledger",
            help="Typed belief-ledger diagnostics and operations",
            setup_fn=setup_cli,
            handler_fn=build_cli_handler(runtime),
            description="Inspect, verify, replay, export, and evaluate belief-ledger data",
        )

    ctx._belief_ledger_pramana_runtime = runtime
    ctx._belief_ledger_pramana_registered = True
    logger.info(
        "belief-ledger-pramana registered mode=%s hermes=%s",
        compatibility.mode.value,
        compatibility.hermes_version or "unknown",
    )


__all__ = ["register"]
