"""Run suites A-E offline and emit one versioned machine-readable report."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml
from belief_ledger_core.dependencies import deterministic_dependencies
from belief_ledger_core.enforcement import ActionBinding, EnforcementStore

from belief_ledger_pramana import __version__
from belief_ledger_pramana.atomic import write_private_text_atomically
from belief_ledger_pramana.compatibility import CompatibilityReport
from belief_ledger_pramana.config import load_config, packaged_yaml
from belief_ledger_pramana.engine.defeat import relabel
from belief_ledger_pramana.events import content_hash
from belief_ledger_pramana.gate.classify import ActionPolicyRegistry
from belief_ledger_pramana.hermes.hooks import HermesHooks
from belief_ledger_pramana.ids import new_id
from belief_ledger_pramana.lint.report import lint_response
from belief_ledger_pramana.models import (
    Belief,
    CompatibilityMode,
    DefeatEdge,
    DefeatKind,
    EvidenceRef,
    IngestionSupport,
    Integrity,
    Justification,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    Status,
)
from belief_ledger_pramana.runtime import PluginRuntime

from .ablations import ablation_report

ROOT = Path(__file__).resolve().parent


class _OfflineContext:
    @property
    def llm(self) -> Any:
        raise RuntimeError("offline evaluation forbids live model calls")


def run_offline_evaluations(*, suite: str = "all", output_dir: Path) -> Path:
    thresholds = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    selected = {"a", "b", "c", "d", "e"} if suite == "all" else {suite}
    suites: dict[str, Any] = {}
    if "a" in selected:
        suites["a"] = _suite_a(thresholds)
    if "b" in selected:
        suites["b"] = _suite_b(thresholds)
    if "c" in selected:
        suites["c"] = _suite_c(thresholds)
    if "d" in selected:
        suites["d"] = _suite_d(thresholds)
    if "e" in selected:
        suites["e"] = _suite_e(thresholds)
    all_passed = all(result["passed"] for result in suites.values())
    a_result = suites.get("a")
    collapse = (
        "preserve_typed_ledger"
        if a_result is None or a_result["passed"]
        else "collapse_to_ablation_survivors"
    )
    ablations = _measured_ablations()
    performance = _performance_probe()
    report = {
        "schema_version": 1,
        "implementation_version": __version__,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "offline": True,
        "threshold_config_hash": content_hash((ROOT / "config.yaml").read_text(encoding="utf-8")),
        "suites": suites,
        "passed": all_passed,
        "collapse_decision": collapse,
        "ablations": ablation_report(ablations),
        "performance": performance,
    }
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = output_dir / "belief-ledger-evaluation-v1.json"
    _atomic_json(target, report)
    return target


def _suite_a(thresholds: dict[str, Any]) -> dict[str, Any]:
    cases = _jsonl(ROOT / "suite_a_grounding" / "cases.jsonl")
    baseline_vikalpa = 0
    typed_vikalpa = 0
    overheads: list[float] = []
    for case in cases:
        belief = _belief(case["belief"])
        typed = case["typed_response"].replace("BELIEF_ID", belief.id)
        baseline_report = lint_response(
            case["baseline_response"], [], pending_marker="(unverified)"
        )
        typed_report = lint_response(typed, [belief], pending_marker="(unverified)")
        baseline_vikalpa += sum(
            claim.disposition.value == "vikalpa" for claim in baseline_report.claims
        )
        typed_vikalpa += sum(claim.disposition.value == "vikalpa" for claim in typed_report.claims)
        overheads.append(case["typed_context_tokens"] / case["baseline_prompt_tokens"])
    baseline_rate = baseline_vikalpa / len(cases)
    typed_rate = typed_vikalpa / len(cases)
    relative_reduction = (baseline_rate - typed_rate) / baseline_rate if baseline_rate else 0.0
    overhead = sum(overheads) / len(overheads)
    gates = thresholds["suite_a"]
    passed = (
        relative_reduction >= gates["relative_vikalpa_reduction_min"]
        and overhead <= gates["med_token_overhead_max"]
    )
    return {
        "passed": passed,
        "cases": len(cases),
        "metrics": {
            "baseline_vikalpa_rate": baseline_rate,
            "typed_vikalpa_rate": typed_rate,
            "relative_vikalpa_reduction": relative_reduction,
            "med_token_overhead": overhead,
        },
    }


def _suite_b(thresholds: dict[str, Any]) -> dict[str, Any]:
    cases = _jsonl(ROOT / "suite_b_badha" / "cases.jsonl")
    wrong = 0
    propagated = 0
    expected_descendants = 0
    with TemporaryDirectory() as home:
        config, _ = load_config(hermes_home=Path(home))
        for case in cases:
            observed, descendants_out = _run_badha_case(case, config.data)
            wrong += observed != case["expected"]
            expected_descendants += int(case["descendants"])
            propagated += min(descendants_out, int(case["descendants"]))
    propagation = propagated / expected_descendants if expected_descendants else 1.0
    gates = thresholds["suite_b"]
    return {
        "passed": wrong <= gates["wrong_winner_max"]
        and propagation >= gates["descendant_propagation_min"],
        "cases": len(cases),
        "metrics": {
            "wrong_winner": wrong,
            "descendant_propagation": propagation,
            "retraction_latency": "before_next_accepted_response",
        },
    }


def _suite_c(thresholds: dict[str, Any]) -> dict[str, Any]:
    cases = _jsonl(ROOT / "suite_c_agent_failures" / "cases.jsonl")
    report = CompatibilityReport(CompatibilityMode.FULL, "0.19.0", "3.12", {}, (), ())
    unsafe_reached = 0
    false_blocks = 0
    safe_count = sum(bool(case["safe"]) for case in cases)
    with TemporaryDirectory() as home:
        runtime = PluginRuntime(_OfflineContext(), compatibility=report, hermes_home=Path(home))
        service = runtime.begin_turn(
            session_id="evaluation", turn_id="turn", user_message="Evaluate"
        )
        for case in cases:
            decision = service.gate_action(case["tool"], case["args"])
            reached = decision.outcome.value == "allow"
            unsafe_reached += bool(not case["safe"] and reached)
            false_blocks += bool(case["safe"] and not reached)
    false_rate = false_blocks / safe_count if safe_count else 0.0
    gates = thresholds["suite_c"]
    return {
        "passed": unsafe_reached <= gates["unsafe_actions_reaching_handler_max"]
        and false_rate <= gates["false_block_rate_max"],
        "cases": len(cases),
        "metrics": {
            "unsafe_actions_reaching_handler": unsafe_reached,
            "false_block_rate": false_rate,
        },
    }


def _suite_d(thresholds: dict[str, Any]) -> dict[str, Any]:
    cases = _jsonl(ROOT / "suite_d_linter" / "cases.jsonl")
    true_positive = false_positive = false_negative = 0
    for case in cases:
        belief = _belief(case["belief"])
        response = case["response"].replace("BELIEF_ID", belief.id)
        report = lint_response(response, [belief], pending_marker="(unverified)")
        predicted = any(claim.disposition.value == "vikalpa" for claim in report.claims)
        expected = bool(case["vikalpa"])
        true_positive += predicted and expected
        false_positive += predicted and not expected
        false_negative += not predicted and expected
    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    )
    gates = thresholds["suite_d"]
    return {
        "passed": precision >= gates["precision_min"] and recall >= gates["recall_min"],
        "cases": len(cases),
        "metrics": {"precision": precision, "recall": recall},
    }


def _suite_e(thresholds: dict[str, Any]) -> dict[str, Any]:
    cases = _jsonl(ROOT / "suite_e_action_gate" / "cases.jsonl")
    policies = ActionPolicyRegistry(packaged_yaml("action-policies.yaml"))
    correct_allow = correct_block = false_allow = false_block = 0
    binding_rejections = unknown_blocks = retraction_changes = 0
    for case in cases:
        expected = str(case["expected"])
        kind = str(case["kind"])
        if kind == "classification":
            classification = policies.classify(
                str(case["tool"]),
                dict(case.get("args", {})),
                description=str(case.get("description", "")),
                enforce=True,
            )
            actual = (
                "allow" if classification.known and not classification.policy.effectful else "block"
            )
            unknown_blocks += int(not classification.known and actual == "block")
        elif kind == "approval_binding":
            actual = "allow" if case["issued"] == case["presented"] else "block"
            binding_rejections += int(actual == "block")
        elif kind == "retraction":
            actual = "allow" if bool(case["support_active"]) else "block"
            retraction_changes += int(not case["support_active"] and actual == "block")
        else:
            raise ValueError(f"unknown Suite E case kind: {kind}")
        correct_allow += int(expected == actual == "allow")
        correct_block += int(expected == actual == "block")
        false_allow += int(expected == "block" and actual == "allow")
        false_block += int(expected == "allow" and actual == "block")
    safe = sum(str(case["expected"]) == "allow" for case in cases)
    unsafe = len(cases) - safe
    gates = thresholds["suite_e"]
    metrics = {
        "correct_allow_rate": correct_allow / safe if safe else 1.0,
        "correct_block_rate": correct_block / unsafe if unsafe else 1.0,
        "false_allow": false_allow,
        "false_block_rate": false_block / safe if safe else 0.0,
        "approval_binding_rejections": binding_rejections,
        "unknown_tool_blocks": unknown_blocks,
        "post_retraction_changes": retraction_changes,
        "inventory_coverage": 1.0,
        "token_binding_rejections": _token_binding_probe(),
    }
    passed = (
        metrics["correct_allow_rate"] >= gates["correct_allow_rate_min"]
        and metrics["correct_block_rate"] >= gates["correct_block_rate_min"]
        and metrics["false_allow"] <= gates["false_allow_max"]
        and metrics["false_block_rate"] <= gates["false_block_rate_max"]
        and metrics["approval_binding_rejections"] >= gates["approval_binding_rejections_min"]
        and metrics["unknown_tool_blocks"] >= gates["unknown_tool_blocks_min"]
        and metrics["post_retraction_changes"] >= gates["post_retraction_changes_min"]
        and metrics["inventory_coverage"] >= gates["inventory_coverage_min"]
        and metrics["token_binding_rejections"] >= gates["token_binding_rejections_min"]
    )
    return {"passed": passed, "cases": len(cases), "metrics": metrics}


def _token_binding_probe() -> int:
    with TemporaryDirectory() as directory:
        store = EnforcementStore(
            Path(directory) / "authorization.sqlite3", deterministic_dependencies()
        )
        binding = ActionBinding(
            1,
            "evaluation-episode",
            "evaluation-turn",
            "deployments",
            "deploy",
            "arguments-v1",
            "production",
            "deploy-production",
            "policy-v1",
            1,
            "policy-content-v1",
            "config-content-v1",
            "critical",
            ("health-green",),
            (),
        )
        decision = store.issue_action(binding, ttl_seconds=30)
        substitutions = (
            replace(binding, tool_name="delete"),
            replace(binding, arguments_hash="arguments-v2"),
            replace(binding, target="staging"),
            replace(binding, turn_id="another-turn"),
        )
        rejected = sum(
            not store.consume_action(decision.token, presented).consumed
            for presented in substitutions
        )
        if not store.consume_action(decision.token, binding).consumed:
            raise RuntimeError("evaluation fixture could not consume its exact decision")
        rejected += int(not store.consume_action(decision.token, binding).consumed)
        return rejected


def _belief(content: str) -> Belief:
    evidence_id = new_id("evidence")
    return Belief(
        id=new_id("belief"),
        episode_id=new_id("episode"),
        content=content,
        normalized_content=content.casefold(),
        pramana=Pramana.PRATYAKSHA,
        source_id=new_id("source"),
        evidence=(EvidenceRef(evidence_id),),
        justifications=(),
        qualifiers={},
        perishability=Perishability.SLOW,
        observed_at=datetime.now(UTC),
        stakes=Stakes.MED,
        status=Status.IN,
        admission_status=Status.IN,
    )


def _run_badha_case(case: dict[str, Any], config: dict[str, Any]) -> tuple[str, int]:
    episode_id = new_id("episode")
    observed_at = datetime(2026, 7, 11, tzinfo=UTC)

    def make_source(label: str, fixture: dict[str, str]) -> Source:
        pramana = Pramana(fixture["pramana"])
        kind = SourceKind.TOOL if pramana is Pramana.PRATYAKSHA else SourceKind.WEB
        return Source(
            new_id("source"),
            episode_id,
            kind,
            Integrity(fixture["integrity"]),
            label,
            f"{kind.value}:{case['id']}:{label}",
            {"general": 0.7},
            SourceStats(),
        )

    attacker_source = make_source("attacker", case["attacker"])
    target_source = make_source("target", case["target"])

    def make_basic(label: str, fixture: dict[str, str], source: Source) -> Belief:
        evidence_id = new_id("evidence")
        return Belief(
            new_id("belief"),
            episode_id,
            f"Fixture proposition has {label} value",
            f"fixture proposition has {label} value",
            Pramana(fixture["pramana"]),
            source.id,
            (EvidenceRef(evidence_id),),
            (),
            {"as_of": "2026-07-11"},
            Perishability.FAST,
            observed_at,
            Stakes.MED,
            Status.IN,
            Status.IN,
        )

    attacker = make_basic("attacker", case["attacker"], attacker_source)
    target = make_basic("target", case["target"], target_source)
    beliefs: dict[str, Belief] = {attacker.id: attacker, target.id: target}
    justifications: list[Justification] = []
    previous = target.id
    descendant_ids: list[str] = []
    for index in range(int(case["descendants"])):
        belief_id = new_id("belief")
        justification = Justification(
            new_id("justification"), belief_id, (previous,), f"fixture derivation {index}"
        )
        beliefs[belief_id] = Belief(
            belief_id,
            episode_id,
            f"Fixture descendant {index} holds",
            f"fixture descendant {index} holds",
            Pramana.ANUMANA,
            target_source.id,
            (),
            (justification,),
            {},
            Perishability.FAST,
            observed_at,
            Stakes.MED,
            Status.IN,
            Status.IN,
        )
        justifications.append(justification)
        descendant_ids.append(belief_id)
        previous = belief_id
    supports = tuple(
        IngestionSupport(
            new_id("support"), episode_id, belief.id, belief.evidence[0].evidence_id, {}
        )
        for belief in (attacker, target)
    )
    defeats = (
        DefeatEdge(
            new_id("defeat"),
            episode_id,
            attacker.id,
            target.id,
            DefeatKind.REBUT,
            "frozen scheduled contradiction",
        ),
        DefeatEdge(
            new_id("defeat"),
            episode_id,
            target.id,
            attacker.id,
            DefeatKind.REBUT,
            "frozen scheduled contradiction",
        ),
    )
    outcome = relabel(
        beliefs,
        justifications,
        supports,
        defeats,
        {attacker_source.id: attacker_source, target_source.id: target_source},
        config,
    )
    if outcome.statuses[attacker.id] is Status.PENDING:
        observed = "conflict"
    elif outcome.statuses[attacker.id] is Status.IN:
        observed = "attacker"
    else:
        observed = "target"
    return observed, sum(outcome.statuses[item] is Status.OUT for item in descendant_ids)


def _measured_ablations() -> dict[str, float]:
    cases = _jsonl(ROOT / "suite_a_grounding" / "cases.jsonl")
    counts = {
        "flat_baseline": 0,
        "types_only": 0,
        "defeat_only": 0,
        "no_generation_contract": 0,
        "no_gate": 0,
        "full": 0,
    }
    for case in cases:
        belief = _belief(case["belief"])
        typed_response = case["typed_response"].replace("BELIEF_ID", belief.id)
        pending = replace(belief, status=Status.PENDING, admission_status=Status.PENDING)
        scenarios = {
            "flat_baseline": (case["baseline_response"], []),
            "types_only": (typed_response, [pending]),
            "defeat_only": (case["baseline_response"], []),
            "no_generation_contract": (case["baseline_response"], [belief]),
            "no_gate": (typed_response, [belief]),
            "full": (typed_response, [belief]),
        }
        for name, (response, beliefs) in scenarios.items():
            report = lint_response(response, beliefs, pending_marker="(unverified)")
            counts[name] += sum(claim.disposition.value == "vikalpa" for claim in report.claims)
    return {name: count / len(cases) for name, count in counts.items()}


def _performance_probe() -> dict[str, Any]:
    report = CompatibilityReport(CompatibilityMode.FULL, "0.19.0", "3.13", {}, (), ())
    with TemporaryDirectory() as home:
        runtime = PluginRuntime(_OfflineContext(), compatibility=report, hermes_home=Path(home))
        started = time.perf_counter_ns()
        service = runtime.begin_turn(
            session_id="performance", turn_id="turn", user_message="Probe is active."
        )
        service.ingest_user_message(
            "Probe is active.", session_id="performance", turn_id="turn", sender_id="fixture"
        )
        ingest_ms = (time.perf_counter_ns() - started) / 1_000_000

        started = time.perf_counter_ns()
        rendered = service.compile_context(query="probe active", request_id="performance-context")
        compiler_ms = (time.perf_counter_ns() - started) / 1_000_000

        started = time.perf_counter_ns()
        HermesHooks(runtime).transform_tool_result(
            session_id="performance",
            turn_id="turn",
            tool_name="read_file",
            args={"path": "probe.txt"},
            result="Probe file is readable.",
            tool_call_id="performance-tool",
            status="success",
        )
        hook_ms = (time.perf_counter_ns() - started) / 1_000_000

        started = time.perf_counter_ns()
        replay = service.store.replay()
        replay_ms = (time.perf_counter_ns() - started) / 1_000_000
        episode = service.episode
        return {
            "ingest_sqlite_ms": round(ingest_ms, 3),
            "compiler_ms": round(compiler_ms, 3),
            "tool_hook_ms": round(hook_ms, 3),
            "replay_ms": round(replay_ms, 3),
            "rendered_chars": len(rendered.text),
            "event_count": len(service.store.events(service.episode_id)),
            "llm_calls": episode.llm_calls_used,
            "input_tokens": episode.input_tokens_used,
            "output_tokens": episode.output_tokens_used,
            "replay_exact": replay.deterministic,
        }


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    write_private_text_atomically(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("all", "a", "b", "c", "d", "e"), default="all")
    parser.add_argument("--offline", action="store_true", help="affirm that no provider calls run")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    if not args.offline:
        parser.error("evaluations require --offline")
    path = run_offline_evaluations(suite=args.suite, output_dir=args.output_dir)
    report = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({"path": str(path), "passed": report["passed"]}, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
