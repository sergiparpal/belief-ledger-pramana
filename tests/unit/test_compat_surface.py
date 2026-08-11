"""The safety net for Stage 7's moves: `belief_ledger_pramana`'s import surface is pinned.

`belief_ledger_pramana` is a 1.x compatibility contract (ADR 0007) and `plugin.yaml` names it as
the Hermes entry point, but before this test nothing asserted that any symbol stayed importable
from it — `tests/core/test_public_api.py` exercises `belief_ledger_core` alone. That gap is
recorded as F-03, and this file closes it.

`tests/fixtures/compat_surface.json` is the checked-in expectation. Regenerate it deliberately,
never to make a failing test pass: a name disappearing from it is a breaking change to a
compatibility contract and needs the deprecation path in `docs/python-api.md`.

This lives in `tests/unit/` rather than `tests/core/`. The `core-no-adapters` CI job runs
`tests/core` against a venv holding only `packages/core`, where `belief_ledger_pramana` is not
installed at all — putting the pin there would break that job's isolation, which is the property it
exists to prove.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path

import pytest

import belief_ledger_pramana

EXPECTED = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "compat_surface.json").read_text(encoding="utf-8")
)


def _observed() -> dict[str, list[str] | None]:
    surface: dict[str, list[str] | None] = {}
    names = [
        belief_ledger_pramana.__name__,
        *sorted(
            module.name
            for module in pkgutil.walk_packages(
                belief_ledger_pramana.__path__, belief_ledger_pramana.__name__ + "."
            )
        ),
    ]
    for name in names:
        module = importlib.import_module(name)
        exported = getattr(module, "__all__", None)
        surface[name] = sorted(exported) if exported is not None else None
    return surface


def test_no_module_disappears_from_the_compatibility_surface() -> None:
    missing = sorted(set(EXPECTED) - set(_observed()))

    assert missing == [], (
        "these modules are no longer importable from belief_ledger_pramana; removing one is a "
        "breaking change to a 1.x compatibility contract"
    )


def test_no_exported_name_disappears_from_any_module() -> None:
    observed = _observed()
    lost: list[str] = []
    for module, names in EXPECTED.items():
        if not names or module not in observed:
            continue
        current = set(observed[module] or ())
        lost.extend(f"{module}.{name}" for name in names if name not in current)

    assert lost == [], "these names are no longer exported; see docs/compat-surface.md"


def test_the_snapshot_matches_exactly() -> None:
    """Additions are allowed but must be recorded, so the snapshot never drifts from reality."""
    assert _observed() == EXPECTED, (
        "regenerate tests/fixtures/compat_surface.json deliberately, and only after confirming "
        "the change is additive or has a documented deprecation path"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_recorded_module_still_imports(name: str) -> None:
    assert importlib.import_module(name) is not None


def test_the_four_promised_names_are_the_package_level_surface() -> None:
    """What `belief_ledger_pramana.__all__` promises, as opposed to what happens to be reachable."""
    assert sorted(belief_ledger_pramana.__all__) == [
        "Pramana",
        "Stakes",
        "Status",
        "__version__",
    ]


def test_the_plugin_entry_point_target_is_importable() -> None:
    """`plugin.yaml` and `pyproject.toml` both name this module; the host imports it by name."""
    from belief_ledger_pramana.plugin import register

    assert callable(register)
