#!/usr/bin/env python3
"""Generate checksums, dependency inventory, and an SPDX 2.3 JSON SBOM."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT_PACKAGE = "belief-ledger-pramana"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_graph() -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]]]:
    environment = default_environment()
    environment["extra"] = ""
    queue = [ROOT_PACKAGE]
    packages: dict[str, dict[str, Any]] = {}
    relationships: list[tuple[str, str]] = []
    while queue:
        requested = canonicalize_name(queue.pop(0))
        if requested in packages:
            continue
        distribution = importlib.metadata.distribution(requested)
        name = canonicalize_name(distribution.metadata["Name"])
        requirements: list[str] = []
        for raw_requirement in distribution.requires or ():
            requirement = Requirement(raw_requirement)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            dependency = canonicalize_name(requirement.name)
            requirements.append(dependency)
            relationships.append((name, dependency))
            if dependency not in packages:
                queue.append(dependency)
        packages[name] = {
            "name": distribution.metadata["Name"],
            "version": distribution.version,
            "requires": sorted(set(requirements)),
            "license": distribution.metadata.get("License") or "NOASSERTION",
        }
    return packages, sorted(set(relationships))


def _spdx_id(name: str) -> str:
    return "SPDXRef-Package-" + re.sub(r"[^A-Za-z0-9.-]", "-", name)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    artifacts = sorted(path for path in args.dist.resolve().iterdir() if path.is_file())
    checksums = {path.name: _sha256(path) for path in artifacts}
    output.joinpath("checksums.sha256").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in checksums.items()),
        encoding="utf-8",
    )
    output.joinpath("checksums.sha256").chmod(0o600)

    packages, relationships = _runtime_graph()
    dependency_report = {
        "schema_version": 1,
        "root": ROOT_PACKAGE,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "artifact_checksums": checksums,
        "packages": packages,
        "relationships": [
            {"from": parent, "to": dependency, "type": "depends_on"}
            for parent, dependency in relationships
        ],
    }
    _write_json(output / "dependency-report.json", dependency_report)

    created = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    namespace_seed = hashlib.sha256(
        json.dumps(checksums, sort_keys=True).encode("utf-8")
    ).hexdigest()
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "belief-ledger-pramana-1.0.0rc1",
        "documentNamespace": f"https://example.invalid/spdx/belief-ledger-pramana/{namespace_seed}",
        "creationInfo": {"created": created, "creators": ["Tool: generate_release_evidence.py"]},
        "packages": [
            {
                "name": item["name"],
                "SPDXID": _spdx_id(name),
                "versionInfo": item["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": item["license"],
                "copyrightText": "NOASSERTION",
            }
            for name, item in sorted(packages.items())
        ],
        "relationships": [
            {
                "spdxElementId": _spdx_id(parent),
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": _spdx_id(dependency),
            }
            for parent, dependency in relationships
        ],
    }
    _write_json(output / "sbom.spdx.json", sbom)
    print(json.dumps({"checksums": checksums, "packages": len(packages)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
