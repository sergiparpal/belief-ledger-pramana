from __future__ import annotations

import json
from pathlib import Path

from evaluations.report import run_offline_evaluations


def test_all_offline_evaluation_gates_pass(tmp_path: Path) -> None:
    path = run_offline_evaluations(suite="all", output_dir=tmp_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["passed"]
    assert report["collapse_decision"] == "preserve_typed_ledger"
    assert report["suites"]["b"]["metrics"]["wrong_winner"] == 0
    assert report["suites"]["c"]["metrics"]["unsafe_actions_reaching_handler"] == 0
    assert report["suites"]["d"]["metrics"]["precision"] >= 0.9
    assert report["suites"]["d"]["metrics"]["recall"] >= 0.85
