from __future__ import annotations

import importlib
from importlib import resources


def test_complete_host_neutral_surface_imports_without_adapter_modules() -> None:
    modules = (
        "belief_ledger_core.models",
        "belief_ledger_core.events",
        "belief_ledger_core.ids",
        "belief_ledger_core.store",
        "belief_ledger_core.migrations",
        "belief_ledger_core.projections",
        "belief_ledger_core.engine.defeat",
        "belief_ledger_core.ingestion.adapters",
        "belief_ledger_core.gate.decision",
        "belief_ledger_core.lint.report",
        "belief_ledger_core.verification.scheduler",
        "belief_ledger_core.context.render",
        "belief_ledger_core.llm.client",
    )
    imported = [importlib.import_module(name) for name in modules]
    assert all(module.__name__.startswith("belief_ledger_core") for module in imported)
    assert resources.files("belief_ledger_core.data").joinpath("defaults.yaml").is_file()
