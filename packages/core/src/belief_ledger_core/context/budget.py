"""Deterministic character budgeting."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CharacterBudget:
    maximum: int
    parts: list[str] = field(default_factory=list)
    used: int = 0
    truncated: bool = False

    def add(self, text: str, *, mandatory: bool = False) -> bool:
        separator = "\n" if self.parts else ""
        needed = len(separator) + len(text)
        if self.used + needed <= self.maximum:
            if separator:
                self.parts.append(separator)
            self.parts.append(text)
            self.used += needed
            return True
        self.truncated = True
        if mandatory:
            # Safety-sensitive entries are atomic. A partial conflict or
            # retraction line is more misleading than an explicit truncation
            # sentinel supplied by the renderer.
            return False
        return False

    def render(self) -> str:
        return "".join(self.parts)
