"""CLI entry point for the inspection MCP server."""

from __future__ import annotations

import argparse
from pathlib import Path

from belief_ledger_core import BeliefLedger

from .proxy import BeliefLedgerMcp, McpMode
from .server import create_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="belief-ledger-mcp")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=tuple(item.value for item in McpMode),
        default=McpMode.INSPECTION.value,
    )
    args = parser.parse_args(argv)
    mode = McpMode(args.mode)
    if mode is McpMode.PROXY:
        parser.error("proxy mode requires a programmatically configured complete upstream")
    application = BeliefLedgerMcp(BeliefLedger.open(state_root=args.state_root), mode=mode)
    create_server(application).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
