from __future__ import annotations

from itertools import product

import pytest
from belief_ledger_core.contracts import (
    EnforcementProfile,
    HostCapabilities,
    negotiate_profile,
)
from belief_ledger_core.dependencies import deterministic_dependencies
from belief_ledger_core.runtime import CapabilityShortfall, LedgerRuntime

CAPABILITY_FIELDS = tuple(
    name for name in HostCapabilities.__dataclass_fields__ if name != "schema_version"
)


def _capabilities(bits: tuple[bool, ...]) -> HostCapabilities:
    return HostCapabilities(**dict(zip(CAPABILITY_FIELDS, bits, strict=True)))


def test_profile_negotiation_complete_truth_table() -> None:
    for bits in product((False, True), repeat=len(CAPABILITY_FIELDS)):
        capabilities = _capabilities(bits)
        supported = [
            profile for profile in EnforcementProfile if not capabilities.missing_for(profile)
        ]
        expected_maximum = supported[-1]
        assert capabilities.maximum_profile() is expected_maximum

        for requested in EnforcementProfile:
            first = negotiate_profile(capabilities, requested)
            assert first == negotiate_profile(capabilities, requested)
            assert first.missing == capabilities.missing_for(requested)
            assert first.effective is requested
            assert first.downgraded is False
            downgraded = negotiate_profile(capabilities, requested, allow_diagnostic_downgrade=True)
            expected = requested if not first.missing else expected_maximum
            assert downgraded.effective is expected
            assert downgraded.downgraded is (expected is not requested)


def test_enforcing_shortfall_fails_closed_and_downgrade_is_persisted(tmp_path) -> None:
    with pytest.raises(CapabilityShortfall, match="CAPABILITY_SHORTFALL:strict"):
        LedgerRuntime(
            tmp_path,
            deterministic_dependencies(),
            HostCapabilities(pre_action_gate=True),
            requested_profile=EnforcementProfile.STRICT,
        )

    runtime = LedgerRuntime(
        tmp_path,
        deterministic_dependencies(),
        HostCapabilities(pre_action_gate=True),
        requested_profile=EnforcementProfile.STRICT,
        allow_diagnostic_downgrade=True,
    )
    from belief_ledger_core.contracts import EpisodeContext

    runtime.start_episode(EpisodeContext.normalize(turn_id="turn-1"))
    selected = runtime.events[-1].as_dict()["payload"]
    assert selected["requested"] == "strict"
    assert selected["effective"] == "action_enforce"
    assert selected["downgraded"] is True
    assert "PROFILE_DOWNGRADED" in selected["reason_codes"]


def test_observe_mode_reports_shortfall_without_claiming_enforcement() -> None:
    selection = negotiate_profile(HostCapabilities(), EnforcementProfile.STRICT, observe_only=True)
    assert selection.requested is EnforcementProfile.STRICT
    assert selection.effective is EnforcementProfile.OBSERVE
    assert selection.missing
    assert selection.reason_codes[0] == "OBSERVE_MODE"
