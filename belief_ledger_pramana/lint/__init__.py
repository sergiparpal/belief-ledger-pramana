"""Final-output vikalpa detection and bounded enforcement."""

from .enforce import enforce_report
from .report import lint_response

__all__ = ["enforce_report", "lint_response"]
