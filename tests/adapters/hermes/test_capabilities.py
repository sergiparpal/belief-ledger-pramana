from __future__ import annotations

from belief_ledger_pramana.compatibility import CompatibilityReport
from belief_ledger_pramana.models import CompatibilityMode


def test_hermes_capabilities_do_not_claim_strict_dispatch_or_delivery() -> None:
    report = CompatibilityReport(
        CompatibilityMode.FULL,
        "0.19.0",
        "3.13",
        {"register_hook": True},
        (),
        (),
    )
    capabilities = report.host_capabilities()
    assert capabilities.maximum_profile().value == "accepted_final"
    assert capabilities.pre_action_gate
    assert capabilities.per_request_context
    assert capabilities.accepted_final_transform
    assert not capabilities.atomic_action_token_consume
    assert not capabilities.exclusive_final_output_gate
    assert not capabilities.buffered_stream_delivery
    assert not capabilities.bound_approval
    assert not capabilities.tool_inventory
