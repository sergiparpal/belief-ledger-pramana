from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from belief_ledger_pramana.compatibility import CompatibilityReport
from belief_ledger_pramana.models import CompatibilityMode
from belief_ledger_pramana.runtime import PluginRuntime


@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    total_tokens: int = 15
    cost_usd: float | None = None


@dataclass
class FakeStructuredResult:
    parsed: Any
    provider: str = "scripted"
    model: str = "scripted-model"
    usage: FakeUsage = field(default_factory=FakeUsage)


class ScriptedLlm:
    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def queue(self, value: Any) -> None:
        self.responses.append(value)

    def complete_structured(self, **kwargs: Any) -> FakeStructuredResult:
        self.calls.append(kwargs)
        if not self.responses:
            raise RuntimeError("no scripted response")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return FakeStructuredResult(value)


class FakeContext:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}
        self.hooks: dict[str, list[Any]] = {}
        self.middleware: dict[str, list[Any]] = {}
        self.commands: dict[str, dict[str, Any]] = {}
        self.cli_commands: dict[str, dict[str, Any]] = {}
        self.manifest = SimpleNamespace(
            name="belief-ledger-pramana",
            key="belief-ledger-pramana",
            source="user",
        )
        self._manager = SimpleNamespace(
            _hooks=self.hooks,
            _middleware=self.middleware,
            _plugin_tool_names=set(),
            _plugin_commands={},
            _cli_commands={},
        )
        self._llm = ScriptedLlm()

    @property
    def llm(self) -> ScriptedLlm:
        return self._llm

    def register_tool(self, **kwargs: Any) -> None:
        self.tools[kwargs["name"]] = kwargs
        self._manager._plugin_tool_names.add(kwargs["name"])

    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks.setdefault(name, []).append(callback)

    def register_middleware(self, name: str, callback: Any) -> None:
        self.middleware.setdefault(name, []).append(callback)

    def register_command(self, name: str, handler: Any, **kwargs: Any) -> None:
        self.commands[name] = {"handler": handler, **kwargs}
        self._manager._plugin_commands[name] = self.commands[name]

    def register_cli_command(self, **kwargs: Any) -> None:
        self.cli_commands[kwargs["name"]] = kwargs
        self._manager._cli_commands[kwargs["name"]] = kwargs


@pytest.fixture
def compatibility() -> CompatibilityReport:
    return CompatibilityReport(
        mode=CompatibilityMode.FULL,
        hermes_version="0.18.2",
        python_version="3.12.3",
        capabilities={},
        errors=(),
        warnings=(),
    )


@pytest.fixture
def fake_ctx() -> FakeContext:
    return FakeContext()


@pytest.fixture
def runtime(
    tmp_path: Path,
    fake_ctx: FakeContext,
    compatibility: CompatibilityReport,
) -> PluginRuntime:
    result = PluginRuntime(fake_ctx, compatibility=compatibility, hermes_home=tmp_path)
    result.ensure_initialized()
    return result
