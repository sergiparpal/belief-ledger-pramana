from __future__ import annotations

import pytest
from belief_ledger_core.manifest import (
    InventoryStatus,
    ManifestError,
    ToolDescriptor,
    ToolPolicyManifest,
    canonicalize_schema,
    schema_digest,
)

from belief_ledger_pramana.config import packaged_yaml


def _rule(**overrides):
    value = {
        "id": "deploy",
        "revision": "deploy-v1",
        "effectful": True,
        "base_stakes": "high",
        "target_fields": ["environment"],
        "preconditions": ["health"],
        "approval_policy": "required",
        "minimum_source_integrity": "trusted",
        "canonicalization_version": 1,
        "exact": ["deploy"],
    }
    value.update(overrides)
    return value


def _manifest(*rules):
    return ToolPolicyManifest.load(
        {"schema_version": 2, "canonicalization_version": 1, "rules": list(rules)}
    )


def test_v1_registry_normalizes_without_changing_classification() -> None:
    manifest = ToolPolicyManifest.load(packaged_yaml("action-policies.yaml"))
    assert manifest.source_schema_version == 1
    assert manifest.schema_version == 2
    assert manifest.match("write_file").effectful is True
    assert manifest.match("read_file").effectful is False


def test_manifest_rejects_unknown_duplicate_unanchored_and_ambiguous_rules() -> None:
    with pytest.raises(ManifestError, match="unknown manifest fields"):
        ToolPolicyManifest.load({"schema_version": 2, "rules": [], "surprise": True})
    assert (
        ToolPolicyManifest.load(
            {"schema_version": 2, "rules": [], "surprise": True}, mode="observe"
        ).rules
        == ()
    )
    with pytest.raises(ManifestError, match="duplicate exact"):
        _manifest(_rule(), _rule(id="other", revision="other-v1"))
    with pytest.raises(ManifestError, match="anchored"):
        _manifest(_rule(exact=[], pattern="deploy"))
    ambiguous = _manifest(
        _rule(id="one", exact=[], pattern="^deploy.*$"),
        _rule(id="two", revision="v2", exact=[], pattern="^.*deploy$"),
    )
    with pytest.raises(ManifestError, match="ambiguous"):
        ambiguous.match("deploy")


def test_exact_match_precedes_pattern_and_namespace_is_respected() -> None:
    manifest = _manifest(
        _rule(id="pattern", exact=[], pattern="^deploy.*$", priority=100),
        _rule(id="exact", revision="exact-v1", effectful=False, exact=["deploy"]),
        _rule(id="other-host", revision="host-v1", namespace="other", exact=["deploy"]),
    )
    assert manifest.match("deploy").id == "exact"
    assert manifest.match("deploy", "other").id == "other-host"


def test_schema_digest_ignores_order_and_description_but_detects_effect_changes() -> None:
    left = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "description": "old words",
        "properties": {"target": {"type": "string"}, "count": {"type": "number"}},
        "required": ["target"],
    }
    reordered = {
        "required": ["target"],
        "properties": {
            "count": {"type": "number"},
            "target": {"description": "new", "type": "string"},
        },
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
    }
    changed = {**reordered, "required": ["target", "count"]}
    assert schema_digest(left) == schema_digest(reordered)
    assert schema_digest(left) != schema_digest(changed)
    assert "description" not in str(canonicalize_schema(left))


def test_schema_canonicalization_resolves_local_refs_and_rejects_remote_or_cycles() -> None:
    schema = {
        "$defs": {"target": {"type": "string", "description": "ignored"}},
        "properties": {"target": {"$ref": "#/$defs/target"}},
    }
    assert canonicalize_schema(schema)["properties"]["target"] == {"type": "string"}
    with pytest.raises(ManifestError, match="only local"):
        schema_digest({"$ref": "https://example.test/schema"})
    with pytest.raises(ManifestError, match="cyclic"):
        schema_digest({"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"})
    with pytest.raises(ManifestError, match="dialect"):
        schema_digest({"$schema": "unknown"})


def test_inventory_reports_coverage_drift_unknown_and_incompleteness() -> None:
    schema = {"type": "object", "properties": {"environment": {"type": "string"}}}
    digest = schema_digest(schema)
    manifest = _manifest(_rule(input_schema_digest=digest))
    covered = ToolDescriptor.create("deploy", schema)
    drifted = ToolDescriptor.create("deploy", {"type": "object", "required": ["environment"]})
    unknown = ToolDescriptor.create("mystery", {})
    assert (
        manifest.classify_inventory((covered,), complete=True)[0].status
        is InventoryStatus.EFFECTFUL
    )
    assert (
        manifest.classify_inventory((drifted,), complete=True)[0].status is InventoryStatus.DRIFTED
    )
    incomplete = manifest.classify_inventory((unknown,), complete=False)
    assert [item.reason_code for item in incomplete] == ["NO_POLICY", "INVENTORY_INCOMPLETE"]
    scaffold = manifest.scaffold(unknown)
    assert scaffold["active"] is False
    assert scaffold["preconditions"] == ["operator_review_required"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"effectful": "true"}, "effectful"),
        ({"base_stakes": "urgent"}, "base_stakes"),
        ({"exact": [], "pattern": 7}, "pattern"),
        ({"exact": [], "pattern": "^([$"}, "pattern is invalid"),
        ({"exact": []}, "exact names or a pattern"),
        ({"approval_policy": 1}, "approval_policy must be a string"),
        ({"approval_policy": "sometimes"}, "approval_policy must be"),
        ({"minimum_source_integrity": 1}, "minimum_source_integrity must be a string"),
        ({"minimum_source_integrity": "root"}, "minimum_source_integrity is invalid"),
        ({"canonicalization_version": True}, "must be an integer"),
        ({"canonicalization_version": 2}, "unsupported policy canonicalization"),
        ({"namespace": 1}, "namespace"),
        ({"priority": True}, "priority"),
        ({"active": "yes"}, "active"),
        ({"input_schema_digest": "bad"}, "SHA-256"),
        ({"id": ""}, "policy id"),
        ({"revision": "bad\nrevision"}, "revision"),
        ({"exact": "deploy"}, "exact must be a list"),
        ({"exact": ["deploy", "deploy"]}, "contains duplicates"),
    ],
)
def test_manifest_rule_fields_are_strictly_typed(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ManifestError, match=message):
        _manifest(_rule(**overrides))


def test_manifest_rejects_missing_fields_invalid_versions_and_duplicate_identifiers() -> None:
    with pytest.raises(ManifestError, match="schema_version"):
        ToolPolicyManifest.load({"schema_version": True, "rules": []})
    with pytest.raises(ManifestError, match="canonicalization_version"):
        ToolPolicyManifest.load({"schema_version": 2, "canonicalization_version": 2, "rules": []})
    with pytest.raises(ManifestError, match="missing fields"):
        ToolPolicyManifest.load({"schema_version": 2, "rules": [{"id": "incomplete"}]})
    with pytest.raises(ManifestError, match="must be a mapping"):
        ToolPolicyManifest.load({"schema_version": 2, "rules": ["invalid"]})
    with pytest.raises(ManifestError, match="unknown policy fields"):
        _manifest(_rule(surprise=True))
    with pytest.raises(ManifestError, match="duplicate policy id"):
        _manifest(_rule(), _rule(exact=["other"]))
    with pytest.raises(ManifestError, match="ambiguous duplicate"):
        _manifest(
            _rule(id="first", exact=[], pattern="^deploy.*$"),
            _rule(id="second", revision="second-v1", exact=[], pattern="^deploy.*$"),
        )


def test_v1_and_schema_canonicalization_reject_malformed_edge_cases() -> None:
    with pytest.raises(ManifestError, match="requires rules"):
        ToolPolicyManifest.load({"schema_version": 1})
    with pytest.raises(ManifestError, match="must be a mapping"):
        ToolPolicyManifest.load({"schema_version": 1, "rules": ["invalid"]})
    with pytest.raises(ManifestError, match="must be a boolean"):
        ToolPolicyManifest.load(
            {
                "schema_version": 1,
                "rules": [{"allow_human_approval": "false"}],
            }
        )
    with pytest.raises(ManifestError, match="canonicalization version"):
        canonicalize_schema({}, version=2)
    with pytest.raises(ManifestError, match="unresolved"):
        canonicalize_schema({"$ref": "#/$defs/missing"})

    draft_2020 = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"value": {"type": "string"}},
        "$ref": "#/$defs/value",
        "minLength": 2,
    }
    assert canonicalize_schema(draft_2020) == {
        "allOf": [
            {"type": "string"},
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "minLength": 2,
            },
        ]
    }
    canonical = canonicalize_schema(
        {
            "type": ["string", "null"],
            "enum": ["b", "a"],
            "minimum": 1.0,
            "prefixItems": [{"type": "string"}, {"type": "number"}],
        }
    )
    assert canonical["enum"] == ["a", "b"]
    assert canonical["prefixItems"] == [{"type": "string"}, {"type": "number"}]
    assert canonical["minimum"] == 1


def test_manifest_pattern_inventory_and_serialization_edges() -> None:
    manifest = _manifest(_rule(exact=[], pattern="^deploy-.*$"))
    assert manifest.match("deploy-now").id == "deploy"
    assert manifest.as_dict()["schema_version"] == 2
    duplicate = ToolDescriptor.create("deploy-now", {})
    with pytest.raises(ManifestError, match="duplicate tool inventory"):
        manifest.classify_inventory((duplicate, duplicate), complete=True)
