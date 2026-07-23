#!/usr/bin/env python3
"""Keep public product language aligned with tested capability claims."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADLINE = "Evidence-backed policy enforcement for AI agents"
PUBLIC_FILES = (
    "README.md",
    "pyproject.toml",
    "plugin.yaml",
    "after-install.md",
    "RELEASE_NOTES.md",
)
RESTRICTED = {
    "compliance": re.compile(r"\bcompliance\b", re.IGNORECASE),
    "prompt-injection defense": re.compile(
        r"\bprompt[- ]injection\s+(?:defen[cs]e|protection|layer)\b", re.IGNORECASE
    ),
    "sandbox": re.compile(r"\bsandbox(?:ed|ing)?\b", re.IGNORECASE),
}
NEGATION = re.compile(
    r"\b(?:not|no|never|does\s+not|isn't|is\s+not|requires?\s+external)\b", re.IGNORECASE
)


def claim_violations(text: str) -> list[str]:
    violations: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for label, pattern in RESTRICTED.items():
            if pattern.search(line) and not NEGATION.search(line):
                violations.append(f"line {line_number}: unqualified {label} claim")
    return violations


def main() -> int:
    failures: list[str] = []
    for relative in PUBLIC_FILES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        if HEADLINE.casefold() not in text.casefold():
            failures.append(f"{relative}: missing approved headline")
        failures.extend(f"{relative}: {item}" for item in claim_violations(text))
    if failures:
        print("\n".join(failures))
        return 1
    print(f"product claims valid across {len(PUBLIC_FILES)} public metadata files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
