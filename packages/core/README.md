# belief-ledger-core

Evidence-backed policy enforcement for AI agents.

This host-neutral distribution owns the generic `BeliefLedger` Python API, immutable contracts,
domain model, event-sourced SQLite store, reasoning, policy manifests, bound approvals and permits,
output evaluation, verification, and replay. It imports with every adapter absent.

```python
from pathlib import Path
from belief_ledger_core import BeliefLedger, EpisodeContext

ledger = BeliefLedger.open(state_root=Path(".belief-ledger"))
episode = ledger.start_episode(EpisodeContext.normalize(session_id="s", turn_id="t"))
```

Core produces decisions and can issue/consume opaque in-process permits; it never executes arbitrary
handlers or claims delivery through a host sink. `record_approval()` requires an authenticated
adapter/control plane. `LedgerRuntime` remains a 1.x fixture compatibility facade and is not the
recommended API. Repository scripts build local artifacts but do not publish them.
