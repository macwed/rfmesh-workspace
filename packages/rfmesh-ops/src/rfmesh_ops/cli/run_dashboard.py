"""CLI entrypoint for the rfmesh-ops dashboard.

Usage:

    rfmesh-ops --connect ws://10.0.0.1:9001 --layout trench

Reads ``--connect URL`` (the FusionService DashboardPubSub WebSocket)
and ``--layout {trench, debug, minimal}`` (one of the bound
``DashboardLayout`` constants). Builds the dashboard, runs it on the
default asyncio loop, and blocks on ``plt.show()`` so the figure stays
open until the operator closes it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import matplotlib.pyplot as plt

from rfmesh_ops.client import DashboardClient
from rfmesh_ops.dashboard import Dashboard
from rfmesh_ops.layouts import LAYOUTS_BY_NAME

_LOG = logging.getLogger("rfmesh_ops")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rfmesh-ops",
        description="rfmesh live ops dashboard.",
    )
    parser.add_argument(
        "--connect",
        required=True,
        help="WebSocket URL of the FusionService dashboard channel (e.g. ws://10.0.0.1:9001).",
    )
    parser.add_argument(
        "--layout",
        choices=sorted(LAYOUTS_BY_NAME),
        default="trench",
        help="Which DashboardLayout to use (default: trench).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser


async def _async_main(url: str, layout_name: str) -> None:
    layout = LAYOUTS_BY_NAME[layout_name]
    client = DashboardClient.websocket(url)
    dashboard = Dashboard(client, layout)
    # plt.show(block=False) is non-blocking on interactive backends;
    # the dashboard's asyncio task drives draws via draw_idle.
    plt.show(block=False)
    await dashboard.run()


def run_dashboard_main(argv: list[str] | None = None) -> int:
    """The console-script entrypoint.

    Returns 0 on clean exit, non-zero on error. Exposed for pytest as
    well as the ``rfmesh-ops`` console script.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(_async_main(args.connect, args.layout))
    except KeyboardInterrupt:
        _LOG.info("dashboard interrupted by operator")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(run_dashboard_main())
