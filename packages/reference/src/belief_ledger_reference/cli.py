"""Compatibility entry point for the versioned gateway JSONL protocol."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import BinaryIO, TextIO

from belief_ledger_gateway.protocol import serve_jsonl as gateway_serve_jsonl


def serve_jsonl(source: TextIO | BinaryIO, destination: TextIO, *, state_root: Path) -> int:
    return gateway_serve_jsonl(source, destination, state_root=state_root)


def main() -> int:
    parser = argparse.ArgumentParser(prog="belief-ledger-reference")
    parser.add_argument("--state-root", type=Path, default=Path(".belief-ledger-reference"))
    args = parser.parse_args()
    return serve_jsonl(sys.stdin, sys.stdout, state_root=args.state_root)


if __name__ == "__main__":
    raise SystemExit(main())
