from __future__ import annotations

from pathlib import Path

from belief_ledger_core.contracts import EnforcementProfile
from belief_ledger_core.dependencies import deterministic_dependencies
from belief_ledger_reference import ReferenceRunner

from belief_ledger_pramana.compatibility import CompatibilityReport
from belief_ledger_pramana.models import CompatibilityMode


def test_adapters_report_explicit_profile_difference(tmp_path: Path) -> None:
    hermes = CompatibilityReport(
        CompatibilityMode.FULL,
        "0.19.0",
        "3.13",
        {"register_hook": True},
        (),
        (),
    ).host_capabilities()
    reference = ReferenceRunner(tmp_path, dependencies=deterministic_dependencies()).capabilities
    assert hermes.maximum_profile() is EnforcementProfile.ACCEPTED_FINAL
    assert reference.maximum_profile() is EnforcementProfile.STRICT
    assert set(hermes.missing_for(EnforcementProfile.STRICT)) == {
        "atomic_action_token_consume",
        "exclusive_final_output_gate",
        "buffered_stream_delivery",
        "bound_approval",
        "tool_inventory",
    }


def test_deployment_contract_matches_across_fake_and_reference_adapters() -> None:
    from examples.deployment_gate.run import run_fake, run_reference

    assert run_reference() == run_fake()
