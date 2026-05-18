"""A3 artifact capture: programmatic demo-replay end-to-end exercise.

Bypasses the ``rfmesh-demo-replay`` CLI's headless-fix-sink bug (the
relay only forwards fixes when the dashboard queue is also wired). We
construct the orchestrator with dashboard enabled, plumb a local
subscriber that snapshots every BearingReport + FixEvent, and after
each beat:

  * render the trench-demo dashboard with the accumulated state and
    savefig() a PNG under docs/demo/artifacts/;
  * append the fix (if any) to a per-beat fixes JSON;
  * serialise the fix to CoT XML.

Run from repo root:
    uv run python scripts/capture_demo_artifacts.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend before any pyplot import

from rfmesh_contracts import BearingReport, FixEvent
from rfmesh_cot.markers import fix_event_to_cot_xml
from rfmesh_demo_replay.replay import ReplayOrchestrator
from rfmesh_demo_replay.scenario import ScenarioLoader
from rfmesh_ops.client import DashboardClient
from rfmesh_ops.dashboard import Dashboard
from rfmesh_ops.layouts import DEMO_LAYOUT_TRENCH

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = REPO_ROOT / "scenarios" / "trench_demo.yaml"
ARTIFACT_DIR = REPO_ROOT / "docs" / "demo" / "artifacts"


class _CaptureSubscriber:
    """Stash every BearingReport / FixEvent as it comes through."""

    def __init__(self) -> None:
        self.bearings: list[BearingReport] = []
        self.fixes: list[FixEvent] = []
        self.statuses: list[object] = []

    async def send(self, message: object) -> None:
        if isinstance(message, BearingReport):
            self.bearings.append(message)
        elif isinstance(message, FixEvent):
            self.fixes.append(message)
        else:
            self.statuses.append(message)


async def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    scenario = ScenarioLoader().load(SCENARIO_PATH)
    print(f"Loaded scenario {scenario.scenario_id} with {len(scenario.beats)} beats")

    # Build orchestrator with dashboard enabled so fix_sink relay fires.
    fix_sink: asyncio.Queue[FixEvent] = asyncio.Queue()
    orchestrator = ReplayOrchestrator(
        scenario,
        pessimism_factor=1.0,
        enable_cot=False,
        enable_dashboard=True,
        fix_sink=fix_sink,
    )

    # Snapshot subscriber.
    capture = _CaptureSubscriber()

    # Patch in our subscriber via _setup hook -- we have to run _setup
    # before the .run() coroutine starts so the dashboard pubsub exists.
    orchestrator._setup()
    pubsub = orchestrator.dashboard_pubsub
    assert pubsub is not None
    pubsub.add_subscriber(capture)  # type: ignore[arg-type]

    print("Running orchestrator...")
    await orchestrator.run()

    print(f"Captured {len(capture.bearings)} bearings, {len(capture.fixes)} fixes")
    print(f"fix_sink queue size: {fix_sink.qsize()}")

    # Drain fix_sink for completeness.
    fixes_from_sink: list[FixEvent] = []
    while not fix_sink.empty():
        fixes_from_sink.append(fix_sink.get_nowait())
    print(f"fix_sink drained: {len(fixes_from_sink)}")

    if not capture.fixes:
        print("WARN: no fixes captured -- aborting artifact emission.")
        return 1

    # Build per-beat dashboard snapshots. Beats are ordered chronologically;
    # snapshot N includes all bearings up to that point plus fixes 0..N.
    # Beat A produces no fix (one bearing), so beats B/C/D yield fixes 1..3.
    beat_letters = ["B", "C", "D"]  # beats producing fixes
    bearings_per_beat = [
        # Beat A: 1 bearing  (node-l1-south alone -> no fix)
        capture.bearings[0:1],
        # Beat B: 2 bearings (west + east)
        capture.bearings[0:3],
        # Beat C: 3 bearings (west + east + south)
        capture.bearings[0:6],
        # Beat D: 4 bearings (+ L2 overwatch)
        capture.bearings[0:9],
    ]
    fixes_per_beat: list[list[FixEvent]] = [
        [],
        capture.fixes[:1],
        capture.fixes[:2],
        capture.fixes[:3],
    ]

    for idx, letter in enumerate(["A", *beat_letters]):
        queue: asyncio.Queue = asyncio.Queue()
        for b in bearings_per_beat[idx]:
            await queue.put(b)
        for fx in fixes_per_beat[idx]:
            await queue.put(fx)
        client = DashboardClient.in_process(queue)
        dashboard = Dashboard(client=client, layout=DEMO_LAYOUT_TRENCH)
        while not queue.empty():
            msg = queue.get_nowait()
            dashboard._dispatch_one(msg)
        png_path = ARTIFACT_DIR / f"trench_demo_beat_{letter}.png"
        dashboard.figure.savefig(png_path, dpi=110, bbox_inches="tight")
        print(f"Wrote {png_path.relative_to(REPO_ROOT)}")

    # Per-fix CoT XML + JSON.
    fixes_json = []
    for idx, fix in enumerate(capture.fixes):
        xml_bytes = fix_event_to_cot_xml(fix)
        xml_path = ARTIFACT_DIR / f"trench_demo_fix_{idx + 1:02d}.xml"
        xml_path.write_bytes(xml_bytes)
        fixes_json.append(
            {
                "fix_id": str(fix.fix_id),
                "t_unix_ns": fix.t_unix_ns,
                "lat_deg": fix.position.lat_deg,
                "lon_deg": fix.position.lon_deg,
                "hae_m": fix.position.hae_m,
                "sigma_m": fix.position.sigma_m,
                "semi_major_m": fix.confidence_ellipse_95.semi_major_m,
                "semi_minor_m": fix.confidence_ellipse_95.semi_minor_m,
                "orientation_deg": fix.confidence_ellipse_95.orientation_deg,
                "gdop": fix.gdop,
                "confidence_level": fix.confidence_level.value,
                "method": fix.method,
                "contributing_nodes": list(fix.contributing_nodes),
                "residuals_deg": list(fix.residuals_deg),
            }
        )
        print(f"  Wrote {xml_path.relative_to(REPO_ROOT)}")

    fixes_json_path = ARTIFACT_DIR / "trench_demo_fixes.json"
    fixes_json_path.write_text(json.dumps(fixes_json, indent=2))
    print(f"Wrote {fixes_json_path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
