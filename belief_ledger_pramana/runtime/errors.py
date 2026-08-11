"""Runtime failure types and the tool-evidence value moved out of the old runtime module."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..ingestion.adapters import AdaptedToolResult
from ..ingestion.tool import (
    PreparedEvidence,
)
from ..models import (
    Evidence,
    Source,
)

logger = logging.getLogger(__name__)


class RuntimeUnavailable(RuntimeError):
    pass


class EpisodeResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    """The normalized, privacy-preserving result of one tool invocation."""

    adapted: AdaptedToolResult
    wrapper_source: Source
    content_source: Source | None
    prepared: PreparedEvidence
    evidence: Evidence
