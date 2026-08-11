"""Runtime package.

Split out of a single 3,233-line `runtime.py` by pure moves (ADR 0015). Every name that was
importable from `belief_ledger_pramana.runtime` stays importable from here — including the
underscore-prefixed helpers, which several tests reach for directly. `__all__` still promises only
the three public names; re-exporting the private ones keeps the move pure rather than making it a
surface change wearing a refactor's clothes.

`tests/unit/test_compat_surface.py` is what holds this fixed while the modules move underneath it.
"""

from .episode_service import EpisodeService
from .errors import EpisodeResolutionError, RuntimeUnavailable, ToolEvidence

# Deliberately re-exported without entering __all__: these are private helpers that predate
# the split and that tests import from this path. Keeping them importable is what makes the
# split a pure move; promoting them to __all__ would widen the promised surface instead.
from .helpers import (  # noqa: F401
    _action_policy_data,
    _apply_source_profile,
    _args_hash,
    _clean,
    _contradiction_payload,
    _correlation,
    _descendant_ids,
    _explicitly_acknowledges_retraction,
    _is_relative_workspace_path,
    _ordered_belief_pair,
    _record_draft,
    _safe_text_hash,
    _source_profile_data,
    _validate_claim_result,
    _validate_contradiction,
    _validate_entailment,
    _validate_rewrite,
)
from .plugin_runtime import PluginRuntime

__all__ = [
    "EpisodeResolutionError",
    "EpisodeService",
    "PluginRuntime",
    "RuntimeUnavailable",
    "ToolEvidence",
]
