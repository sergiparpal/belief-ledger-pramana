#!/usr/bin/env python3
"""Build exact workspace wheels into a fresh run-specific directory."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOTS = {
    "core": ROOT / "packages" / "core",
    "hermes": ROOT,
    "reference": ROOT / "packages" / "reference",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages", nargs="+", choices=tuple(PACKAGE_ROOTS), required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    build_root = ROOT / "build"
    build_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="workspace-", dir=build_root)).resolve()
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to build workspace artifacts")
    artifacts: list[dict[str, str]] = []
    for name in args.packages:
        package_root = PACKAGE_ROOTS[name]
        metadata = tomllib.loads((package_root / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        before = set(output.glob("*.whl"))
        subprocess.run(
            [
                uv,
                "build",
                "--wheel",
                "--out-dir",
                str(output),
                str(package_root),
            ],
            cwd=ROOT,
            check=True,
        )
        created = sorted(set(output.glob("*.whl")) - before)
        if len(created) != 1:
            raise RuntimeError(f"expected one fresh wheel for {name}, found {len(created)}")
        artifacts.append(
            {
                "package": name,
                "distribution": str(metadata["name"]),
                "version": str(metadata["version"]),
                "path": str(created[0].resolve()),
            }
        )
    manifest = {
        "schema_version": 1,
        "output_directory": str(output),
        "artifacts": artifacts,
    }
    target = args.output_manifest.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
