#!/usr/bin/env python
"""Interactive trench-demo dashboard against the simulator.

One-shot script that:
  1. Loads scenarios/trench_demo.yaml.
  2. Spins up a ReplayOrchestrator in live mode (dashboard subscriber on).
  3. Builds a DashboardClient.in_process around the orchestrator's
     dashboard queue.
  4. Constructs a Dashboard with DEMO_LAYOUT_TRENCH (the 4x2 BoTH3
     jury layout).
  5. Pumps the matplotlib GUI event loop concurrently with the
     async orchestrator + dashboard tasks so the figure actually
     refreshes live during the demo.
  6. After the orchestrator's beats finish, holds the figure open
     until the operator closes the window or hits Ctrl-C.

Run:

    uv run python scripts/show_dashboard.py

Press Ctrl-C in the terminal or close the figure window to stop.

This is a quick-look helper, not a production CLI. The proper CLI is
`rfmesh-ops --connect ws://...` which talks to a running FusionService
over a WebSocket; this script just bolts both halves into one process
for the operator's eyes-on session before hardware lands.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from rfmesh_cot.publisher import PyTAKCotPublisher
from rfmesh_demo_replay.replay import ReplayOrchestrator
from rfmesh_demo_replay.scenario import ScenarioLoader
from rfmesh_ops.client import DashboardClient
from rfmesh_ops.dashboard import Dashboard
from rfmesh_ops.layouts import LAYOUTS_BY_NAME

# Pick an interactive backend explicitly so this runs whether the
# default matplotlib config picks Agg or Qt or Wayland. TkAgg is the
# safest default across Fedora / Ubuntu / Wayland desktops. Calling
# matplotlib.use() after pyplot import is supported on matplotlib
# >= 3.3 as long as no figure has been created yet (which is true at
# this point — figures are created inside _async_main).
matplotlib.use("TkAgg")

_LOG = logging.getLogger("show_dashboard")

_GUI_PUMP_INTERVAL_S = 0.05  # 20 Hz GUI refresh while running


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="show_dashboard",
        description=(
            "Bring up the rfmesh-ops dashboard against the simulator's "
            "trench-demo scenario, interactively. Quick-look helper for "
            "the operator's eyes-on session before live hardware lands."
        ),
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("scenarios/trench_demo.yaml"),
        help="Scenario YAML to run (default: scenarios/trench_demo.yaml).",
    )
    parser.add_argument(
        "--layout",
        choices=sorted(LAYOUTS_BY_NAME),
        default="trench",
        help="Dashboard layout (default: trench = the BoTH3 jury layout).",
    )
    parser.add_argument(
        "--cot-url",
        type=str,
        default=None,
        help=(
            "Optional PyTAK-style CoT endpoint URL. When set, the orchestrator "
            "publishes every FixEvent as a CoT XML hostile-emitter marker to "
            "this endpoint. Examples: "
            "'tcp://localhost:8087' (FreeTAKServer on the same machine), "
            "'tcp://<phone-IP>:8087' (direct to an ATAK device's CoT input), "
            "'udp://239.2.3.1:6969' (TAK multicast group). "
            "Omit to disable CoT (default — no TAK output)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity (default INFO).",
    )
    return parser.parse_args(argv)


async def _gui_pump(stop_event: asyncio.Event) -> None:
    """Pump matplotlib's GUI event loop so the figure refreshes live.

    Without this, matplotlib's draw_idle() calls accumulate but the
    GUI never gets a chance to process its event queue, so the
    window appears frozen until the asyncio loop finally exits.
    Running this concurrently with the orchestrator + dashboard
    fixes the "window pops up for half a second and disappears"
    symptom.
    """
    while not stop_event.is_set():
        if not plt.get_fignums():
            # User closed the window. Signal everyone to stop.
            stop_event.set()
            return
        plt.pause(0.001)  # pump one round of GUI events
        await asyncio.sleep(_GUI_PUMP_INTERVAL_S)


async def _async_main(scenario_path: Path, layout_name: str, cot_url: str | None) -> int:
    scenario = ScenarioLoader().load(scenario_path)
    cot_publisher: PyTAKCotPublisher | None = None
    if cot_url is not None:
        cot_publisher = PyTAKCotPublisher(cot_url)
        _LOG.info("show_dashboard: CoT publisher will emit to %s", cot_url)
    orchestrator = ReplayOrchestrator(
        scenario,
        enable_cot=cot_url is not None,
        enable_dashboard=True,  # the in-process dashboard subscriber
        cot_publisher=cot_publisher,
    )
    layout = LAYOUTS_BY_NAME[layout_name]

    # Bring up the orchestrator first so its DashboardPubSub queue
    # exists; only then can the dashboard subscribe.
    orch_task = asyncio.create_task(orchestrator.run(), name="orchestrator")
    while orchestrator.dashboard_queue is None:
        await asyncio.sleep(0.01)
        if orch_task.done():
            return 1

    client = DashboardClient.in_process(orchestrator.dashboard_queue)
    dashboard = Dashboard(client, layout)
    plt.show(block=False)
    _LOG.info(
        "show_dashboard: figure open. Scenario %s, layout %s.",
        scenario.scenario_id,
        layout_name,
    )
    _LOG.info("show_dashboard: ~60 s of demo beats follow; window stays open after.")

    stop_event = asyncio.Event()
    dashboard_task = asyncio.create_task(dashboard.run(), name="dashboard")
    gui_task = asyncio.create_task(_gui_pump(stop_event), name="gui_pump")

    # Wait for the orchestrator's beats to finish OR the user to
    # close the figure window (whichever comes first).
    done, _pending = await asyncio.wait(
        {orch_task, gui_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if orch_task in done:
        _LOG.info(
            "show_dashboard: demo beats finished. "
            "Figure stays open — close the window or Ctrl-C to exit."
        )
    else:
        _LOG.info("show_dashboard: figure window closed by operator.")

    # Hold the figure open while the operator inspects it. Keep
    # pumping GUI events; stop when the window closes.
    while plt.get_fignums():
        await asyncio.sleep(_GUI_PUMP_INTERVAL_S)
        plt.pause(0.001)

    stop_event.set()
    for task in (dashboard_task, gui_task, orch_task):
        if not task.done():
            task.cancel()
    for task in (dashboard_task, gui_task, orch_task):
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await task
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s %(message)s")
    try:
        return asyncio.run(_async_main(args.scenario, args.layout, args.cot_url))
    except KeyboardInterrupt:
        _LOG.info("show_dashboard: interrupted by operator")
        return 0


if __name__ == "__main__":
    sys.exit(main())
