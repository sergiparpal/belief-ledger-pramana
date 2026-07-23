from __future__ import annotations

import json

import pytest

from belief_ledger_pramana.policy_cli import main


@pytest.mark.parametrize(
    "arguments,expected_key,expected_value",
    [
        (["belief-ledger", "policy", "validate", "--format", "json"], "valid", True),
        (["belief-ledger", "policy", "inventory", "--format", "json"], "complete", False),
        (
            ["belief-ledger", "policy", "scaffold", "new_mutation", "--format", "json"],
            "review_required",
            True,
        ),
        (
            ["belief-ledger", "policy", "explain", "read_file", "--format", "json"],
            "matched",
            True,
        ),
    ],
)
def test_standalone_policy_cli_json(
    monkeypatch, capsys, arguments: list[str], expected_key: str, expected_value: object
) -> None:
    monkeypatch.setattr("sys.argv", arguments)
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result[expected_key] == expected_value


def test_standalone_policy_cli_human_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["belief-ledger", "policy", "explain", "unknown"])
    assert main() == 0
    assert '"reason_code": "NO_POLICY"' in capsys.readouterr().out
