"""Idempotent injection for all audited Hermes provider request shapes."""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Any


class ContextInjectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InjectionResult:
    request: dict[str, Any]
    changed: bool


class HermesRequestInjector:
    """Append one authenticated internal block without touching system fields."""

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._marker = re.compile(
            r"<!-- BELIEF_LEDGER_PRAMANA:BEGIN sig=([0-9a-f]{64}) -->\n(.*?)\n<!-- BELIEF_LEDGER_PRAMANA:END -->",
            re.DOTALL,
        )

    def wrap(self, context: str) -> str:
        signature = hmac.new(self._secret, context.encode("utf-8"), hashlib.sha256).hexdigest()
        return (
            f"<!-- BELIEF_LEDGER_PRAMANA:BEGIN sig={signature} -->\n"
            f"{context}\n"
            "<!-- BELIEF_LEDGER_PRAMANA:END -->"
        )

    def inject(self, request: dict[str, Any], *, api_mode: str, context: str) -> InjectionResult:
        copied = copy.deepcopy(request)
        if self._request_has_valid_marker(copied):
            return InjectionResult(copied, False)
        block = self.wrap(context)
        if api_mode == "chat_completions":
            self._inject_chat(copied, block)
        elif api_mode == "anthropic_messages":
            self._inject_anthropic(copied, block)
        elif api_mode == "bedrock_converse":
            self._inject_bedrock(copied, block)
        elif api_mode == "codex_responses":
            self._inject_codex(copied, block)
        else:
            raise ContextInjectionError(f"unknown Hermes api_mode: {api_mode or '<missing>'}")
        return InjectionResult(copied, True)

    def _inject_chat(self, request: dict[str, Any], block: str) -> None:
        message = _last_user(request.get("messages"))
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = f"{content}\n\n{block}"
        elif isinstance(content, list):
            content.append({"type": "text", "text": block})
        else:
            raise ContextInjectionError("chat_completions last user content has an unknown shape")

    def _inject_anthropic(self, request: dict[str, Any], block: str) -> None:
        message = _last_user(request.get("messages"))
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = [
                {"type": "text", "text": content},
                {"type": "text", "text": block},
            ]
        elif isinstance(content, list):
            content.append({"type": "text", "text": block})
        else:
            raise ContextInjectionError("anthropic_messages last user content has an unknown shape")

    def _inject_bedrock(self, request: dict[str, Any], block: str) -> None:
        message = _last_user(request.get("messages"))
        content = message.get("content")
        if not isinstance(content, list):
            raise ContextInjectionError("bedrock_converse last user content must be a block list")
        content.append({"text": block})

    def _inject_codex(self, request: dict[str, Any], block: str) -> None:
        message = _last_user(request.get("input"))
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = [
                {"type": "input_text", "text": content},
                {"type": "input_text", "text": block},
            ]
        elif isinstance(content, list):
            content.append({"type": "input_text", "text": block})
        else:
            raise ContextInjectionError("codex_responses last user input has an unknown shape")

    def _request_has_valid_marker(self, request: dict[str, Any]) -> bool:
        for text in _all_text(request):
            for match in self._marker.finditer(text):
                body = match.group(2)
                expected = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).hexdigest()
                if hmac.compare_digest(match.group(1), expected):
                    return True
        return False


def _last_user(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        raise ContextInjectionError("provider request has no message/input list")
    for item in reversed(value):
        if isinstance(item, dict) and item.get("role") == "user":
            return item
    raise ContextInjectionError("provider request has no user item")


def _all_text(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(_all_text(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_all_text(item))
    return found
