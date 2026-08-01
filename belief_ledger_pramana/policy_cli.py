"""Backward-compatible import delegate for the gateway-owned policy CLI."""

from __future__ import annotations

from belief_ledger_gateway.cli import main as gateway_main


def main() -> int:
    """Delegate without adding warnings or changing JSON output."""

    return gateway_main()


if __name__ == "__main__":
    raise SystemExit(main())
