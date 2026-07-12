from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

import belief_ledger_pramana.compatibility as compatibility
from belief_ledger_pramana.config import packaged_yaml
from belief_ledger_pramana.engine.graph import cycle_path, descendants
from belief_ledger_pramana.engine.priority import priority_trace
from belief_ledger_pramana.engine.retractions import affected_subgraph, notice_expired
from belief_ledger_pramana.gate.classify import ActionPolicyRegistry
from belief_ledger_pramana.ids import is_typed_id, new_id
from belief_ledger_pramana.ingestion.absence import assess_negative_search
from belief_ledger_pramana.ingestion.adapters import ToolAdapterRegistry
from belief_ledger_pramana.ingestion.provenance import (
    independent,
    normalize_url,
    provenance_root,
    registrable_domain,
)
from belief_ledger_pramana.lint.enforce import enforce_report, linter_failure_response
from belief_ledger_pramana.models import (
    Integrity,
    Justification,
    LintClaim,
    LintDisposition,
    LintReport,
    Source,
    SourceKind,
    SourceStats,
    Stakes,
    VerificationMethod,
)
from belief_ledger_pramana.verification.apta import updated_source
from belief_ledger_pramana.verification.methods import method_instruction


def test_compatibility_modes_versions_and_transform_order(monkeypatch) -> None:
    full_ctx = SimpleNamespace(
        register_tool=lambda **kwargs: None,
        register_hook=lambda *args: None,
        register_middleware=lambda *args: None,
        register_command=lambda *args, **kwargs: None,
        register_cli_command=lambda **kwargs: None,
        llm=object(),
    )
    monkeypatch.setattr(compatibility, "_distribution_version", lambda: "0.18.2")
    monkeypatch.setattr(
        compatibility,
        "_host_contract_sets",
        lambda ctx: (set(compatibility.REQUIRED_HOOKS), {"llm_request"}),
    )
    report = compatibility.inspect_host(full_ctx)
    assert report.mode.value == "full"
    assert report.full_conformance

    hook_only = SimpleNamespace(**vars(full_ctx))
    hook_only.register_middleware = None
    degraded = compatibility.inspect_host(hook_only)
    assert degraded.mode.value == "hook_context"
    assert degraded.warnings

    diagnostics = SimpleNamespace(register_tool=None, register_hook=None)
    unavailable = compatibility.inspect_host(diagnostics)
    assert unavailable.mode.value == "diagnostics_only"
    assert unavailable.errors

    assert compatibility._supported_version("0.18.2")
    assert compatibility._supported_version("0.18.9+local")
    assert not compatibility._supported_version("0.18.1")
    assert not compatibility._supported_version("0.19.0")
    assert not compatibility._supported_version("invalid")

    def own():
        return None

    def other():
        return None

    ctx = SimpleNamespace(_manager=SimpleNamespace(_hooks={"transform_llm_output": [own, other]}))
    assert compatibility.transformer_has_precedence(ctx, own)
    assert compatibility.competing_transformers(ctx, own)
    assert not compatibility.transformer_has_precedence(ctx, other)


def test_provenance_roots_and_adapter_families_are_conservative() -> None:
    assert normalize_url("HTTPS://WWW.Example.COM//a#fragment") == "https://www.example.com/a"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert provenance_root(SourceKind.WEB, identity="https://news.example/a").startswith(
        "web:news.example:"
    )
    assert provenance_root(SourceKind.USER, identity="u") == "user:u"
    assert provenance_root(SourceKind.TOOL, identity="t") == "tool:t"
    assert provenance_root(SourceKind.MODEL, identity="m") == "model:m"
    assert provenance_root(SourceKind.LEDGER, identity="l") == "ledger:l"
    assert provenance_root(SourceKind.RETRIEVER, identity="r") == "retriever:r"
    document = provenance_root(SourceKind.DOCUMENT, identity="x", origin="/tmp/x", content=b"same")
    assert document.startswith("document:") and document.endswith(":/tmp/x")
    assert not independent(
        "same", "same", "Claim is true", "Claim is true", near_duplicate_threshold=0.9
    )
    assert independent("a", "b", "Claim is true", "Claim is true", near_duplicate_threshold=0.9)
    assert not independent("a", "b", "alpha", "omega", near_duplicate_threshold=0.99)

    registry = ToolAdapterRegistry()
    cases = (
        ("fetch_url", {"url": "https://example.test/a"}, "web", SourceKind.WEB),
        ("read_file", {"path": "README.md"}, "file", SourceKind.DOCUMENT),
        ("memory_retrieve", {"memory_id": "m"}, "memory", SourceKind.LEDGER),
        ("search_index", {"index": "docs"}, "retrieval", SourceKind.RETRIEVER),
        ("delegate_task", {"agent_id": "a"}, "delegation", SourceKind.MODEL),
        ("pramana_query", {}, "plugin", SourceKind.MODEL),
        ("exec_command", {"cmd": "pwd"}, "execution", None),
        ("opaque_probe", {}, "unknown", None),
    )
    for name, args, family, source_kind in cases:
        adapted = registry.adapt(name, args, '{"ok":true}', status="success", tool_call_id="c")
        assert adapted.adapter == family
        assert adapted.wrapper_source.kind is SourceKind.TOOL
        assert (adapted.content_source.kind if adapted.content_source else None) is source_kind
        assert adapted.successful and adapted.parsed
    failed = registry.adapt("opaque_probe", {}, "", status="failure")
    assert not failed.successful
    assert not failed.parsed


def test_absence_requires_complete_yogyata() -> None:
    good = assess_negative_search(
        search_succeeded=True,
        truncated=False,
        corpus="repository",
        scope="src",
        query="legacy_mode",
        parameters={"case_sensitive": True},
        coverage=0.95,
        recall=0.9,
        min_coverage=0.85,
        min_recall=0.85,
    )
    assert good.admissible
    assert good.validity["search_succeeded"] is True
    mutations = (
        {"search_succeeded": False},
        {"truncated": True},
        {"corpus": ""},
        {"scope": ""},
        {"query": ""},
        {"parameters": {}},
        {"coverage": 0.1},
        {"recall": 0.1},
    )
    base = {
        "search_succeeded": True,
        "truncated": False,
        "corpus": "repository",
        "scope": "src",
        "query": "legacy_mode",
        "parameters": {"case_sensitive": True},
        "coverage": 0.95,
        "recall": 0.9,
        "min_coverage": 0.85,
        "min_recall": 0.85,
    }
    for mutation in mutations:
        assert not assess_negative_search(**{**base, **mutation}).admissible


def test_action_classifier_terminal_and_unknown_edges() -> None:
    registry = ActionPolicyRegistry(packaged_yaml("action-policies.yaml"))
    assert not registry.classify("exec_command", {"cmd": "rg needle"}).policy.effectful
    assert registry.classify("exec_command", {"cmd": "rm target"}).policy.effectful
    assert registry.classify("exec_command", {}).policy.effectful
    assert registry.classify("exec_command", {"cmd": "'broken"}).policy.effectful
    for bypass in (
        "git -C repo commit -m x",
        "find . -exec rm {} \\;",
        "ls $(rm -rf /tmp/probe)",
        "rg --pre evil needle",
        "git diff --ext-diff",
        "find . -fprint0 output.txt",
    ):
        assert registry.classify("exec_command", {"cmd": bypass}).policy.effectful
    assert registry.classify("mystery", {}, enforce=True).policy.effectful
    assert not registry.classify("mystery", {}, enforce=False).policy.effectful
    assert registry.classify("future_lookup", {"query": "x"}).policy.effectful
    assert not registry.classify(
        "future_lookup", {"query": "x"}, unknown_tool_policy="allow_read_only"
    ).policy.effectful
    assert registry.classify("future_publish", {"target": "x"}).policy.effectful
    with pytest.raises(ValueError, match="anchored"):
        ActionPolicyRegistry(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "bad",
                        "pattern": "bad",
                        "base_stakes": "med",
                        "effectful": False,
                        "minimum_priority": "untrusted",
                        "allow_human_approval": False,
                    }
                ],
            }
        )


def test_lint_enforcement_has_bounded_behavior_for_every_stakes_policy() -> None:
    claim = LintClaim("The moon is green.", LintDisposition.VIKALPA)
    failed = LintReport((claim,), False)
    policies = {"low": "annotate", "med": "rewrite_once", "high": "block", "critical": "block"}
    annotated = enforce_report("The moon is green.", failed, stakes=Stakes.LOW, policy=policies)
    assert annotated.replacement and "Grounding warning" in annotated.replacement

    passed = LintReport((replace_lint(claim, LintDisposition.GROUNDED),), True)
    rewritten = enforce_report(
        "bad",
        failed,
        stakes=Stakes.MED,
        policy=policies,
        rewrite_once=lambda value: "good",
        relint=lambda value: passed if value == "good" else failed,
    )
    assert rewritten.passed and rewritten.replacement == "good"
    fallback = enforce_report(
        "The moon is green.",
        failed,
        stakes=Stakes.MED,
        policy=policies,
        rewrite_once=lambda value: (_ for _ in ()).throw(RuntimeError()),
        relint=lambda value: failed,
    )
    assert fallback.replacement and fallback.replacement.count("speculation:") == 1
    second_failure = enforce_report(
        "The moon is green.",
        failed,
        stakes=Stakes.MED,
        policy=policies,
        rewrite_once=lambda value: value,
        relint=lambda value: failed,
    )
    assert second_failure.replacement and "speculation:" in second_failure.replacement
    no_callbacks = enforce_report("The moon is green.", failed, stakes=Stakes.MED, policy=policies)
    assert no_callbacks.replacement and "speculation:" in no_callbacks.replacement
    blocked = enforce_report("bad", failed, stakes=Stakes.HIGH, policy=policies)
    assert blocked.replacement and blocked.replacement.startswith("Response blocked")
    assert "blocked" in linter_failure_response(Stakes.CRITICAL, "x")
    assert "warning" in linter_failure_response(Stakes.MED, "x").casefold()


def replace_lint(claim: LintClaim, disposition: LintDisposition) -> LintClaim:
    return LintClaim(
        claim.text,
        disposition,
        claim.cited_beliefs,
        claim.supporting_beliefs,
        claim.reason,
    )


def test_graph_retractions_apta_methods_and_ids() -> None:
    a, b, c = (new_id("belief") for _ in range(3))
    j1 = Justification(new_id("justification"), b, (a,), "a to b")
    j2 = Justification(new_id("justification"), c, (b,), "b to c")
    assert set(descendants((j1, j2), a)) == {b, c}
    assert set(affected_subgraph((j1, j2), a)) == {b, c}
    assert cycle_path((j1, j2), a, (a,)) == (a, a)
    assert cycle_path((j1, j2), c, (a,)) is None
    assert notice_expired(2, 3, 5)
    assert not notice_expired(2, 3, 4)

    source = Source(
        new_id("source"),
        new_id("episode"),
        SourceKind.WEB,
        Integrity.SEMI,
        "example",
        "web:example",
        {"general": 0.5},
        SourceStats(),
    )
    changed = updated_source(source, confirmed=2, defeated=1)
    assert changed.stats == SourceStats(confirmed=2, defeated=1, samples=3)
    with pytest.raises(ValueError):
        updated_source(source, defeated=-1)
    for method in VerificationMethod:
        assert "claim" in method_instruction(method, "claim")

    identifier = new_id("event")
    assert is_typed_id(identifier, "event")
    assert not is_typed_id(identifier, "belief")
    assert not is_typed_id("bad", "event")
    with pytest.raises(ValueError):
        new_id("unknown")

    from belief_ledger_pramana.models import Belief, Perishability, Pramana, Status

    naive = Belief(
        new_id("belief"),
        source.episode_id,
        "Naive timestamp is invalid",
        "naive timestamp is invalid",
        Pramana.SHABDA,
        source.id,
        (),
        (),
        {},
        Perishability.FAST,
        datetime(2026, 7, 11),
        Stakes.LOW,
        Status.IN,
        Status.IN,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        priority_trace(naive, source, packaged_yaml("defaults.yaml"))
