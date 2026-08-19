"""Doctor's three severities, and the two checks that had no voice before.

`errors` make the adapter unusable, `warnings` degrade the verdict, `notices` never move it.
Getting a fact into the wrong list is not cosmetic: a warning that fires on a healthy deployment
trains operators to ignore the verdict, and a fact in no list at all is indistinguishable from a
fact that is fine.
"""

from __future__ import annotations

from dataclasses import replace as replace_dataclass
from pathlib import Path

from belief_ledger_pramana.contracts import EnforcementProfile, HostCapabilities
from belief_ledger_pramana.hermes.cli import _anchor_doctor_check, doctor
from belief_ledger_pramana.verification.anchors import build_record


def _with_sink(runtime, path: Path):
    data = dict(runtime.config.data)
    data["anchoring"] = {"sink_path": str(path)}
    runtime._config = replace_dataclass(runtime.config, data=data)
    return runtime


def _verdict(report: dict) -> str:
    """Doctor's own rule, applied to one report's lists.

    Asserting on `status` directly is what let the previous replay-budget test pass in vacuum:
    this fixture saturates at `unavailable` for unrelated reasons, so two statuses can be equal
    while the thing under test is broken.
    """
    if report["errors"]:
        return "unavailable"
    return "degraded" if report["warnings"] else "healthy"


# --- the notices list itself ---------------------------------------------------------------


def test_doctor_reports_three_severities(runtime) -> None:
    report = doctor(runtime)

    assert set(report) >= {"status", "checks", "warnings", "errors", "notices"}
    assert isinstance(report["notices"], list)
    assert report["notices"] == sorted(set(report["notices"]))


def test_a_notice_alone_never_degrades_the_verdict() -> None:
    assert _verdict({"errors": [], "warnings": [], "notices": ["anything"]}) == "healthy"
    assert _verdict({"errors": [], "warnings": ["x"], "notices": []}) == "degraded"
    assert _verdict({"errors": ["x"], "warnings": [], "notices": []}) == "unavailable"


# --- A3: anchoring ---------------------------------------------------------------------------


def test_disabled_anchoring_is_a_notice_not_a_warning(runtime) -> None:
    """`sink_path: ""` is documented as the way to disable anchoring.

    Degrading every default deployment for declining an optional control is exactly the noise
    that makes a verdict worthless, so the opt-out must not degrade — but it must still be said,
    because "no anchor configured" and "anchors all match" were previously the same silence.
    """
    check, severity, message = _anchor_doctor_check(runtime)

    assert check["enabled"] is False
    assert severity == "notice"
    assert "anchoring is disabled" in message

    report = doctor(runtime)
    assert any("anchoring is disabled" in item for item in report["notices"])
    assert not any("anchor" in item for item in report["warnings"])


def test_a_configured_but_unusable_sink_is_a_warning(runtime, tmp_path: Path) -> None:
    """Configured and broken is a misconfiguration someone must fix, unlike the opt-out."""
    assert runtime.paths is not None
    _with_sink(runtime, runtime.paths.root / "inside" / "anchors.jsonl")

    check, severity, message = _anchor_doctor_check(runtime)

    assert severity == "warning"
    assert check["error"] == "anchor_unavailable"
    assert "unusable" in message


def test_a_configured_sink_that_was_never_published_to_is_a_notice(runtime, tmp_path: Path) -> None:
    _with_sink(runtime, tmp_path / "outside" / "anchors.jsonl")

    check, severity, message = _anchor_doctor_check(runtime)

    assert check["anchors"] == 0
    assert severity == "notice"
    assert "no chain anchor has ever been published" in message


def test_a_matching_anchor_is_silent_and_reports_its_freshness(runtime, tmp_path: Path) -> None:
    sink_path = tmp_path / "outside" / "anchors.jsonl"
    _with_sink(runtime, sink_path)
    assert runtime.store is not None
    from belief_ledger_pramana.events import utc_now
    from belief_ledger_pramana.hermes.cli import _anchor_sink

    sink = _anchor_sink(runtime)
    sink.publish(
        build_record(
            runtime.store.chain_state(),
            ledger_id=str(runtime.store.database),
            scope="global",
            created_at=utc_now(),
            package_version="test",
        )
    )

    check, severity, message = _anchor_doctor_check(runtime)

    assert check["newest_status"] == "match"
    assert check["anchors"] == 1
    assert check["events_since_newest_anchor"] == 0
    assert severity == "notice"
    assert message == ""


def test_an_anchor_the_chain_no_longer_matches_is_an_error(runtime, tmp_path: Path) -> None:
    """Tamper evidence is the one anchoring outcome that must make doctor unusable.

    A re-chaining attacker leaves `verify-chain` passing, so a mismatch here is the only signal
    doctor has. Publishing a record whose root does not match any local height stands in for the
    tampering: the comparison is what is under test, not the attack.
    """
    sink_path = tmp_path / "outside" / "anchors.jsonl"
    _with_sink(runtime, sink_path)
    assert runtime.store is not None
    from belief_ledger_pramana.events import utc_now
    from belief_ledger_pramana.hermes.cli import _anchor_sink

    state = runtime.store.chain_state()
    forged = replace_dataclass(state, root_hash="0" * 64)
    _anchor_sink(runtime).publish(
        build_record(
            forged,
            ledger_id=str(runtime.store.database),
            scope="global",
            created_at=utc_now(),
            package_version="test",
        )
    )

    check, severity, message = _anchor_doctor_check(runtime)

    assert check["newest_status"] == "mismatch"
    assert severity == "error"
    assert "not the chain that was anchored" in message

    report = doctor(runtime)
    assert any("not the chain that was anchored" in item for item in report["errors"])
    assert report["status"] == "unavailable"


# --- A4: the guarantee asked for versus the guarantee obtained --------------------------------


def test_the_strict_guarantee_shortfall_is_a_notice_on_every_hermes_host(runtime) -> None:
    """Hermes structurally caps at `accepted_final`; that is a fact, not a fault.

    It has to be visible — §6.2 of the review is that the cap reads as a report line nobody
    notices — but degrading forever on a limit no operator can lift would be noise.
    """
    report = doctor(runtime)

    assert report["checks"]["strict_guarantee"]["available"] is False
    assert "atomic_action_token_consume" in report["checks"]["strict_guarantee"]["missing"]
    assert any("cannot provide the strict enforcement guarantee" in i for i in report["notices"])
    assert not any("strict enforcement guarantee" in i for i in report["warnings"])


def test_the_shortfall_list_is_derived_from_the_capability_contract(runtime) -> None:
    """Not a hand-written list that can drift from `missing_for`."""
    report = doctor(runtime)
    expected = runtime.host_capabilities.missing_for(EnforcementProfile.STRICT)

    assert report["checks"]["strict_guarantee"]["missing"] == list(expected)


def test_a_fully_capable_host_reports_no_shortfall_and_no_notice() -> None:
    capable = HostCapabilities(
        per_request_context=True,
        pre_action_gate=True,
        atomic_action_token_consume=True,
        accepted_final_transform=True,
        exclusive_final_output_gate=True,
        buffered_stream_delivery=True,
        bound_approval=True,
        tool_inventory=True,
    )

    assert capable.missing_for(EnforcementProfile.STRICT) == ()


def test_a_downgraded_profile_degrades_the_verdict(runtime) -> None:
    """The gap between the guarantee requested and the one obtained is a real fault.

    Note the asymmetry with the notice above, which is the whole point of splitting them: the cap
    is what this host can do, the downgrade is what this deployment asked for and did not get.
    """
    assert runtime.profile_selection is not None
    runtime.profile_selection = replace_dataclass(
        runtime.profile_selection,
        requested=EnforcementProfile.STRICT,
        downgraded=True,
        missing=("atomic_action_token_consume",),
        reason_codes=("CAPABILITY_SHORTFALL",),
    )

    report = doctor(runtime)

    assert report["checks"]["enforcement_profile"]["downgraded"] is True
    assert any("enforcement profile downgraded" in item for item in report["warnings"])
    assert "atomic_action_token_consume" in "".join(report["warnings"])


def test_an_undowngraded_profile_emits_no_warning(runtime) -> None:
    report = doctor(runtime)

    assert report["checks"]["enforcement_profile"]["downgraded"] is False
    assert not any("enforcement profile downgraded" in item for item in report["warnings"])
