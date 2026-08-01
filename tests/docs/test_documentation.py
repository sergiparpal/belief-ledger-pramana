from __future__ import annotations

from pathlib import Path

from belief_ledger_core import BeliefLedger, EpisodeContext
from belief_ledger_gateway.cli import main


def test_documented_neutral_commands_and_imports(tmp_path: Path, capsys) -> None:
    assert main(["demo", "--format", "json"]) == 0
    capsys.readouterr()
    state = tmp_path / ".belief-ledger"
    assert main(["--state-root", str(state), "init", "--format", "json"]) == 0
    capsys.readouterr()
    assert main(["--state-root", str(state), "policy", "validate", "--format", "json"]) == 0
    capsys.readouterr()
    assert main(["--state-root", str(state), "ledger", "verify-chain", "--format", "json"]) == 0
    capsys.readouterr()
    ledger = BeliefLedger.open(state_root=state)
    episode = ledger.start_episode(EpisodeContext.normalize(session_id="docs", turn_id="1"))
    assert episode.state == "active"


def test_custom_tool_documentation_fixture() -> None:
    from examples.custom_tool_gate.run import run

    result = run()
    assert result["effects"] == 1
    assert result["steps"][-1]["outcome"] == "block"
