#!/usr/bin/env python3
"""Verify and replay a ledger database from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from belief_ledger_pramana.events import to_primitive  # noqa: E402
from belief_ledger_pramana.store import LedgerStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--busy-timeout-ms", type=int, default=5_000)
    args = parser.parse_args()
    store = LedgerStore(args.database, busy_timeout_ms=args.busy_timeout_ms)
    store.verify_hash_chain()
    result = store.replay()
    print(json.dumps(to_primitive(result), indent=2, sort_keys=True))
    return 0 if result.deterministic else 1


if __name__ == "__main__":
    raise SystemExit(main())
