"""ReplayOrchestrator end-to-end (in-process) tests."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest
from rfmesh_contracts import FixEvent
from rfmesh_demo_replay import ReplayOrchestrator, ScenarioLoader
from rfmesh_demo_replay.cli.run_replay import run_replay_main


async def _drain_queue(queue: asyncio.Queue[FixEvent], timeout_s: float = 5.0) -> list[FixEvent]:
    """Drain ``queue`` for up to ``timeout_s`` and return the items."""
    out: list[FixEvent] = []
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0.0:
            return out
        try:
            item = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            return out
        out.append(item)


async def test_orchestrator_produces_bearings_and_fix(
    tiny_scenario_path: Path,
) -> None:
    """A single-beat 2-node scenario fuses to >= 1 FixEvent."""
    scenario = ScenarioLoader().load(tiny_scenario_path)
    fix_sink: asyncio.Queue[FixEvent] = asyncio.Queue()
    orchestrator = ReplayOrchestrator(
        scenario,
        pessimism_factor=1.0,
        enable_cot=False,
        enable_dashboard=True,
        fix_sink=fix_sink,
    )
    await orchestrator.run()

    # The orchestrator's dashboard queue receives both BearingReports
    # (one per active node per beat) and FixEvents.
    pubsub = orchestrator.dashboard_pubsub
    assert pubsub is not None
    fixes = await _drain_queue(fix_sink, timeout_s=2.0)
    assert len(fixes) >= 1, "fusion must publish at least one FixEvent"
    fix = fixes[0]
    # Two nodes contributed to the fix.
    assert len(fix.contributing_nodes) >= 2


async def test_orchestrator_clean_shutdown_on_no_beats(
    tmp_path: Path, tiny_scenario_path: Path
) -> None:
    """An orchestrator with zero beats still runs cleanly to teardown.

    The orchestrator's _walk_beats short-circuits on empty beats; the
    fusion + dashboard tasks are still cancelled and the receivers
    closed. Test passes if ``run`` returns without raising.
    """
    import yaml

    payload = yaml.safe_load(tiny_scenario_path.read_text(encoding="utf-8"))
    payload["demo_beats"] = []
    no_beats = tmp_path / "no_beats.yaml"
    no_beats.write_text(yaml.safe_dump(payload), encoding="utf-8")

    scenario = ScenarioLoader().load(no_beats)
    orchestrator = ReplayOrchestrator(
        scenario,
        enable_cot=False,
        enable_dashboard=False,
    )
    await orchestrator.run()
    # ``run`` completed without raising. The orchestrator's ``_setup``
    # builds the fusion service before the (empty) beat walk, and
    # ``_teardown`` does not null it out -- but the dashboard subscriber
    # discipline guarantees no subscribers remain holding the queue.
    assert orchestrator.fusion_service is not None


async def test_orchestrator_no_cot_uses_no_cot_publisher(
    tiny_scenario_path: Path,
) -> None:
    """The orchestrator's FusionService receives None when CoT is disabled."""
    scenario = ScenarioLoader().load(tiny_scenario_path)
    orchestrator = ReplayOrchestrator(
        scenario,
        enable_cot=False,
        enable_dashboard=False,
    )
    # We do not run it -- just inspect after _setup.
    orchestrator._setup()
    assert orchestrator.fusion_service is not None
    # The publisher slot stays None when ``enable_cot`` is False and no
    # explicit publisher was supplied.
    assert orchestrator.fusion_service._cot_publisher is None
    await orchestrator._teardown()


async def test_orchestrator_pessimism_inflates_noise(
    tiny_scenario_path: Path,
) -> None:
    """A 2.0x pessimism factor raises the noise floor reported by the simulator.

    We compare the receiver's noise floor under pessimism=1.0 vs
    pessimism=2.0 by inspecting the underlying SimulationScenario.
    Asserting that the noise floor (dBFS) under 2.0 is *larger*
    (closer to 0 dBFS, i.e. less negative) than under 1.0.
    """
    scenario = ScenarioLoader().load(tiny_scenario_path)
    honest = ReplayOrchestrator(scenario, pessimism_factor=1.0, enable_dashboard=False)
    pessimistic = ReplayOrchestrator(scenario, pessimism_factor=2.0, enable_dashboard=False)
    try:
        honest._setup()
        pessimistic._setup()
        h_floor = honest._node_contexts["node-west"].receiver._scenario.noise_floor_dbfs
        p_floor = pessimistic._node_contexts["node-west"].receiver._scenario.noise_floor_dbfs
        # 10 * log10(2.0) ~= 3.01 dB inflation.
        assert p_floor == pytest.approx(h_floor + 10.0 * math.log10(2.0), abs=1e-6)
    finally:
        await honest._teardown()
        await pessimistic._teardown()


def test_run_replay_main_returns_zero(tiny_scenario_path: Path) -> None:
    """``rfmesh-demo-replay`` CLI returns 0 on the tiny scenario."""
    rc = run_replay_main(["--scenario", str(tiny_scenario_path), "--headless", "--no-cot"])
    assert rc == 0
