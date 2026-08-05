"""Host-neutral CLI, JSONL service, and owned dispatcher for Belief Ledger."""

from .dispatcher import GatewayDispatcher, HandlerResult
from .protocol import MAX_LINE_BYTES, GatewayService, serve_jsonl

__version__ = "1.0.0rc4"

__all__ = [
    "MAX_LINE_BYTES",
    "GatewayDispatcher",
    "GatewayService",
    "HandlerResult",
    "serve_jsonl",
]
