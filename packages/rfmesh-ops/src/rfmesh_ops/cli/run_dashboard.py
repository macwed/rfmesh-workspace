"""CLI entrypoint for the rfmesh-ops dashboard.

Usage:

    rfmesh-ops --connect ws://10.0.0.1:9001 --layout trench
    rfmesh-ops --connect ws://10.0.0.1:9001 --layout trench \\
               --beat-e-cache cache/beat_e_trench.npz

Reads ``--connect URL`` (the FusionService DashboardPubSub WebSocket)
and ``--layout {trench, debug, minimal}`` (one of the bound
``DashboardLayout`` constants). Builds the dashboard, optionally
pre-populates ``NullSteeringPanel`` from a Beat E preload cache (see
``apps/demo-replay`` and the ``rfmesh-beat-e-preload`` CLI), runs it on
the default asyncio loop, and blocks on ``plt.show()`` so the figure
stays open until the operator closes it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from rfmesh_ops.client import DashboardClient
from rfmesh_ops.dashboard import Dashboard
from rfmesh_ops.layouts import LAYOUTS_BY_NAME
from rfmesh_ops.panels.null_steering import NullSteeringPanel

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
        "--beat-e-cache",
        type=Path,
        default=None,
        help=(
            "Path to a Beat E preload .npz cache (produced by "
            "rfmesh-beat-e-preload). When supplied, the dashboard's "
            "NullSteeringPanel is pre-populated with the cached "
            "engaged-null receive pattern at startup so the operator's "
            "Beat E demo step renders instantly. Optional; if omitted "
            "the panel renders its empty-state placeholder until "
            "operator action populates it."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser


def _preload_beat_e_into_dashboard(dashboard: Dashboard, cache_path: Path) -> None:
    """If a NullSteeringPanel is in the layout, populate it from the cache.

    Reads the ``.npz`` cache directly (no import from rfmesh-demo-replay
    — that would reverse the apps/* → packages/* dependency direction).
    The cache format is documented in
    ``apps/demo-replay/src/rfmesh_demo_replay/beat_e_preload.py``
    docstring; this loader replicates the read side inline.

    No-op if the layout does not include NullSteeringPanel (e.g. the
    MINIMAL layout). Logs a warning if the cache is loaded but the
    panel is not in the layout — the operator probably wanted to use
    the DEBUG or TRENCH layout.
    """
    if not cache_path.exists():
        msg = f"Beat E cache file not found: {cache_path}"
        raise FileNotFoundError(msg)
    with np.load(cache_path, allow_pickle=False) as data:
        azimuths_deg = np.asarray(data["azimuths_deg"], dtype=np.float64)
        engaged_gain_db = np.asarray(data["engaged_gain_db"], dtype=np.float64)
        engaged_depth_db = float(data["engaged_depth_db"])
        baseline_jammer_gain_db = float(data["baseline_jammer_gain_db"])
        engaged_jammer_gain_db = float(data["engaged_jammer_gain_db"])
    jammer_rejection_db = baseline_jammer_gain_db - engaged_jammer_gain_db
    null_panels = [p for p in dashboard.panels if isinstance(p, NullSteeringPanel)]
    if not null_panels:
        _LOG.warning(
            "--beat-e-cache supplied but the active layout does not include "
            "NullSteeringPanel; cache ignored. Use --layout=trench or --layout=debug."
        )
        return
    for panel in null_panels:
        panel.set_pattern_from_arrays(
            azimuths_deg,
            engaged_gain_db,
            claimed_depth_db=engaged_depth_db,
        )
    _LOG.info(
        "Beat E cache preloaded from %s (engaged depth %.2f dB, jammer rejection %.2f dB).",
        cache_path,
        engaged_depth_db,
        jammer_rejection_db,
    )


async def _async_main(url: str, layout_name: str, beat_e_cache: Path | None) -> None:
    layout = LAYOUTS_BY_NAME[layout_name]
    client = DashboardClient.websocket(url)
    dashboard = Dashboard(client, layout)
    if beat_e_cache is not None:
        _preload_beat_e_into_dashboard(dashboard, beat_e_cache)
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
        asyncio.run(_async_main(args.connect, args.layout, args.beat_e_cache))
    except KeyboardInterrupt:
        _LOG.info("dashboard interrupted by operator")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(run_dashboard_main())
