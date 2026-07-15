"""Hermes lifecycle adapters with explicit fail-closed policy boundaries."""

from __future__ import annotations

import logging
from typing import Any

from ..config import packaged_yaml
from ..events import canonical_json, utc_now
from ..gate.classify import ActionPolicyRegistry
from ..ingestion.tool import redacted_content_hash
from ..lint.enforce import linter_failure_response
from ..models import CompatibilityMode, GateOutcome, Health, Stakes, max_stakes
from ..runtime import PluginRuntime
from ..store import EventDraft

logger = logging.getLogger(__name__)


class HermesHooks:
    def __init__(self, runtime: PluginRuntime) -> None:
        self.runtime = runtime

    def pre_llm_call(self, **kwargs: Any) -> dict[str, str] | None:
        try:
            service = self.runtime.begin_turn(**kwargs)
            message = str(kwargs.get("user_message") or "")
            service.ingest_user_message(message, **kwargs)
            if self.runtime.compatibility.mode is CompatibilityMode.HOOK_CONTEXT:
                rendered = service.compile_context(
                    query=message,
                    request_id=str(kwargs.get("turn_id") or "compatibility-turn"),
                )
                return {
                    "context": self.runtime.injector.wrap(
                        rendered.text, binding=str(kwargs.get("turn_id") or "compatibility-turn")
                    )
                }
            if self.runtime.compatibility.mode is CompatibilityMode.DIAGNOSTICS_ONLY:
                return {
                    "context": (
                        "[belief-ledger-pramana: diagnostics-only; strict epistemic enforcement "
                        "is unavailable on this Hermes contract]"
                    )
                }
            return None
        except Exception as exc:
            logger.exception("belief ledger pre_llm_call failed")
            self.runtime.mark_global_failure("pre_llm_call", type(exc).__name__)
            return {
                "context": (
                    "[belief-ledger-pramana degraded: user evidence ingestion failed; do not "
                    "treat absent ledger support as verification]"
                )
            }

    def pre_tool_call(self, **kwargs: Any) -> dict[str, str] | None:
        tool_name = str(kwargs.get("tool_name") or "")
        args = kwargs.get("args")
        if not isinstance(args, dict):
            documented = kwargs.get("arguments")
            args = documented if isinstance(documented, dict) else {}
        try:
            service = self.runtime.service(**kwargs)
            decision = service.gate_action(tool_name, args)
            if self.runtime.compatibility.mode is CompatibilityMode.DIAGNOSTICS_ONLY:
                if decision.reason_code == "READ_ONLY":
                    return None
                return {
                    "action": "block",
                    "message": (
                        "BLOCKED [UNSUPPORTED_HERMES_CONTRACT]: effectful action requires a "
                        "fully compatible belief-ledger runtime"
                    ),
                }
            if (
                self.runtime.health is not Health.HEALTHY
                and decision.reason_code != "READ_ONLY"
                and _fails_closed(self.runtime, decision.stakes)
            ):
                return {
                    "action": "block",
                    "message": (
                        "BLOCKED [LEDGER_DEGRADED]: the ledger is not healthy enough to "
                        "authorize a high-stakes effectful action; run `hermes belief-ledger "
                        "doctor` and repair the reported condition"
                    ),
                }
            if (
                self.runtime.injection_failed(service.episode_id)
                and decision.reason_code != "READ_ONLY"
                and _fails_closed(self.runtime, decision.stakes)
            ):
                return {
                    "action": "block",
                    "message": (
                        "BLOCKED [CONTEXT_INJECTION_FAILED]: epistemic context was not "
                        "delivered; repair diagnostics before an effectful action"
                    ),
                }
            if decision.outcome is GateOutcome.BLOCK:
                suggestion = (
                    f" Suggested observation: {decision.suggested_observation}."
                    if decision.suggested_observation
                    else ""
                )
                return {"action": "block", "message": decision.message + suggestion}
            if decision.outcome is GateOutcome.APPROVE:
                response = {"action": "approve", "message": decision.message}
                if decision.rule_key:
                    response["rule_key"] = decision.rule_key
                return response
            return None
        except Exception as exc:
            logger.exception("belief ledger action gate failed")
            self.runtime.mark_global_failure("pre_tool_call", type(exc).__name__)
            if _known_read_only_low_med(self.runtime, tool_name, args):
                return None
            return {
                "action": "block",
                "message": (
                    f"BLOCKED [GATE_INTERNAL_FAILURE]: policy evaluation failed "
                    f"({type(exc).__name__}); use a read-only diagnostic before retrying"
                ),
            }

    def transform_tool_result(self, **kwargs: Any) -> None:
        try:
            args = kwargs.get("args")
            if not isinstance(args, dict):
                documented = kwargs.get("arguments")
                args = documented if isinstance(documented, dict) else {}
            result = kwargs.get("result")
            result_text = result if isinstance(result, str) else canonical_json(result)
            service = self.runtime.service(**kwargs)
            metadata = {
                key: value
                for key, value in kwargs.items()
                if key not in {"tool_name", "args", "arguments", "result"}
            }
            service.ingest_tool_result(
                str(kwargs.get("tool_name") or "unknown_tool"),
                args,
                result_text,
                **metadata,
            )
        except Exception as exc:
            logger.exception("belief ledger tool-result ingestion failed")
            self.runtime.mark_global_failure("transform_tool_result", type(exc).__name__)
        # Returning None preserves Hermes' post-redaction tool result byte-for-byte.
        return None

    def transform_llm_output(self, **kwargs: Any) -> str | None:
        response = str(kwargs.get("response_text") or "")
        if not response:
            return None
        try:
            service = self.runtime.service(**kwargs)
            if self.runtime.compatibility.mode is CompatibilityMode.DIAGNOSTICS_ONLY:
                return (
                    response
                    + "\n\n[Belief ledger diagnostics-only: this response was not strictly grounded.]"
                )
            if self.runtime.health is not Health.HEALTHY and _fails_closed(
                self.runtime, service.episode.default_stakes
            ):
                return (
                    "Response blocked because the belief ledger is degraded for a high-stakes "
                    "turn. Run `hermes belief-ledger doctor` and retry."
                )
            if self.runtime.injection_failed(service.episode_id) and _fails_closed(
                self.runtime, service.episode.default_stakes
            ):
                return (
                    "Response blocked because epistemic context injection failed for a "
                    "high-stakes turn. Run `hermes belief-ledger doctor` and retry."
                )
            return service.lint_and_enforce(response, **kwargs)
        except Exception:
            logger.exception("belief ledger final-output transform failed")
            try:
                stakes = self.runtime.service(**kwargs).episode.default_stakes
            except Exception:
                stakes = Stakes.HIGH
            return linter_failure_response(stakes, response)

    def post_llm_call(self, **kwargs: Any) -> None:
        try:
            response = str(kwargs.get("assistant_response") or "")
            if response:
                self.runtime.service(**kwargs).record_accepted_response(response, **kwargs)
        except Exception:
            logger.exception("belief ledger post_llm_call accounting failed")

    def pre_verify(self, **kwargs: Any) -> dict[str, str] | None:
        try:
            return self.runtime.service(**kwargs).pre_verify(
                str(kwargs.get("final_response") or ""),
                attempt=int(kwargs.get("attempt") or 0),
                coding=bool(kwargs.get("coding")),
            )
        except Exception:
            logger.exception("belief ledger pre_verify failed")
            return None

    def on_session_start(self, **kwargs: Any) -> None:
        try:
            service = self.runtime.service(**kwargs)
            service.store.append_events(
                service.episode_id,
                [
                    EventDraft(
                        "SESSION_STARTED",
                        "episode",
                        service.episode_id,
                        {"at": utc_now()},
                    )
                ],
            )
        except Exception:
            logger.exception("belief ledger session start failed")

    def on_session_end(self, **kwargs: Any) -> None:
        try:
            self.runtime.ensure_initialized()
            if self.runtime.store:
                self.runtime.store.checkpoint()
        except Exception:
            logger.exception("belief ledger checkpoint failed")

    def on_session_finalize(self, **kwargs: Any) -> None:
        try:
            service = self.runtime.service(**kwargs)
            self.runtime.finalize(service.episode_id, state="finalized", **kwargs)
        except Exception:
            logger.exception("belief ledger session finalization failed")

    def on_session_reset(self, **kwargs: Any) -> None:
        try:
            service = self.runtime.service(**kwargs)
            service.store.append_events(
                service.episode_id,
                [
                    EventDraft(
                        "SESSION_RESET_STARTED",
                        "episode",
                        service.episode_id,
                        {"at": utc_now(), "reason": str(kwargs.get("reason") or "reset")},
                    )
                ],
            )
            self.runtime.finalize(service.episode_id, state="reset", **kwargs)
        except Exception:
            logger.exception("belief ledger session reset failed")

    def subagent_start(self, **kwargs: Any) -> None:
        try:
            parent_kwargs = dict(kwargs)
            parent_kwargs["session_id"] = kwargs.get("parent_session_id")
            service = self.runtime.service(**parent_kwargs)
            service.store.append_events(
                service.episode_id,
                [
                    EventDraft(
                        "SUBAGENT_STARTED",
                        "subagent",
                        str(kwargs.get("child_session_id") or redacted_content_hash(str(kwargs))),
                        {
                            "parent_turn_id": str(kwargs.get("parent_turn_id") or ""),
                            "child_session_id": str(kwargs.get("child_session_id") or ""),
                            "child_role": str(kwargs.get("child_role") or ""),
                            "goal_hash": redacted_content_hash(str(kwargs.get("child_goal") or "")),
                        },
                    )
                ],
            )
        except Exception:
            logger.exception("belief ledger subagent_start failed")

    def subagent_stop(self, **kwargs: Any) -> None:
        try:
            parent_kwargs = dict(kwargs)
            parent_kwargs["session_id"] = kwargs.get("parent_session_id")
            service = self.runtime.service(**parent_kwargs)
            summary = str(kwargs.get("child_summary") or "")
            if summary:
                parent_kwargs.pop("duration_ms", None)
                service.ingest_tool_result(
                    "delegate_task",
                    {
                        "agent_id": str(kwargs.get("child_session_id") or "child"),
                        "role": str(kwargs.get("child_role") or "subagent"),
                    },
                    summary,
                    tool_call_id=str(kwargs.get("child_session_id") or ""),
                    status=str(kwargs.get("child_status") or "completed"),
                    duration_ms=kwargs.get("duration_ms", 0),
                    **parent_kwargs,
                )
        except Exception:
            logger.exception("belief ledger subagent_stop failed")

    def post_approval_response(self, **kwargs: Any) -> None:
        try:
            session_key = str(kwargs.get("session_key") or "")
            if session_key:
                try:
                    current = self.runtime.current_service()
                    self.runtime.bind_approval_session_key(session_key, current.episode_id)
                except Exception:
                    pass
            service = self.runtime.service(**kwargs)
            choice = str(kwargs.get("choice") or "")
            service.store.append_events(
                service.episode_id,
                [
                    EventDraft(
                        "APPROVAL_RESPONSE_RECORDED",
                        "approval",
                        redacted_content_hash(
                            canonical_json(
                                [
                                    str(kwargs.get("command") or ""),
                                    str(kwargs.get("pattern_key") or ""),
                                    choice,
                                ]
                            )
                        ),
                        {
                            "choice": choice,
                            "surface": str(kwargs.get("surface") or ""),
                            "command_hash": redacted_content_hash(str(kwargs.get("command") or "")),
                            "confirmed": choice in {"once", "session", "always"},
                        },
                    )
                ],
            )
        except Exception:
            logger.exception("belief ledger approval response recording failed")


def _known_read_only_low_med(runtime: PluginRuntime, tool_name: str, args: dict[str, Any]) -> bool:
    try:
        registry = ActionPolicyRegistry(packaged_yaml("action-policies.yaml"))
        classification = registry.classify(tool_name, args, enforce=True)
        try:
            default_stakes = runtime.config.default_stakes
        except Exception:
            default_stakes = Stakes.MED
        effective = max_stakes(default_stakes, classification.policy.base_stakes)
        return (
            classification.known
            and not classification.policy.effectful
            and effective in {Stakes.LOW, Stakes.MED}
        )
    except Exception:
        return False


def _fails_closed(runtime: PluginRuntime, stakes: Stakes) -> bool:
    try:
        threshold = str(runtime.config.data["gating"]["fail_closed_at"])
    except Exception:
        threshold = Stakes.HIGH.value
    ranks = {Stakes.LOW: 0, Stakes.MED: 1, Stakes.HIGH: 2, Stakes.CRITICAL: 3}
    return ranks[stakes] >= ranks[Stakes(threshold)]
