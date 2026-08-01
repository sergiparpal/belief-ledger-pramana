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
    "packages/core/README.md",
    "packages/gateway/README.md",
    "packages/reference/README.md",
    "packages/mcp/README.md",
    "packages/hermes/README.md",
)
RESTRICTED = {
    "compliance": re.compile(r"\bcompliance\b", re.IGNORECASE),
    "prompt-injection defense": re.compile(
        r"\bprompt[- ]injection\s+(?:defen[cs]e|protection|layer)\b", re.IGNORECASE
    ),
    "sandbox": re.compile(r"\bsandbox(?:ed|ing)?\b", re.IGNORECASE),
    "exactly-once": re.compile(r"\bexactly[- ]once\b", re.IGNORECASE),
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
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = readme.casefold().find("## host-neutral quickstart")
    integrations = readme.casefold().find("## integrations")
    hermes_pin = readme.find("3ef6bbd201263d354fd83ec55b3c306ded2eb72a")
    if not (0 <= quickstart < integrations <= hermes_pin):
        failures.append(
            "README.md: host-neutral quickstart must precede the Hermes integration pin"
        )
    hermes_doc = (ROOT / "docs/integrations/hermes.md").read_text(encoding="utf-8")
    if "3ef6bbd201263d354fd83ec55b3c306ded2eb72a" not in hermes_doc:
        failures.append("docs/integrations/hermes.md: missing audited Hermes commit")
    mcp_doc = (ROOT / "docs/integrations/mcp.md").read_text(encoding="utf-8").casefold()
    if "bypass" not in mcp_doc or "at most `action_enforce`" not in mcp_doc:
        failures.append("docs/integrations/mcp.md: missing bypass or scoped capability claim")
    quickstart_text = (ROOT / "docs/quickstart.md").read_text(encoding="utf-8").casefold()
    if "pip install belief-ledger" in quickstart_text:
        failures.append("docs/quickstart.md: unpublished packages presented as registry installs")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"product claims valid across {len(PUBLIC_FILES)} public metadata files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
