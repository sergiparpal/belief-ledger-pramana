"""Compatibility re-exports for the host-neutral action gate.

The gate itself lives in `belief_ledger_core.gate.decision`. This adapter deliberately keeps
no second implementation: the previous parallel copy diverged from core on the audited
`args_hash` encoding, so the same tool call produced two different audit digests.
"""

from belief_ledger_core.gate.decision import (
    ActionGate,
    ActionGateReader,
    ActionGateWriter,
    arguments_digest,
)

__all__ = ["ActionGate", "ActionGateReader", "ActionGateWriter", "arguments_digest"]
