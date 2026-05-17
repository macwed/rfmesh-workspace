"""Tests for rfmesh_ops.dashboard -- the live application."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from rfmesh_contracts import BearingReport, FixEvent, NodeStatus
from rfmesh_ops.client import DashboardClient
from rfmesh_ops.dashboard import Dashboard
from rfmesh_ops.layouts import (
    DEMO_LAYOUT_DEBUG,
    DEMO_LAYOUT_MINIMAL,
    DEMO_LAYOUT_TRENCH,
)

_CANCEL_DELAY_S = 0.01


def test_dashboard_builds_figure_with_n_panels_matching_layout() -> None:
    """Each built-in layout produces a figure with len(panels) Axes."""
    for layout in (DEMO_LAYOUT_TRENCH, DEMO_LAYOUT_DEBUG, DEMO_LAYOUT_MINIMAL):
        queue: asyncio.Queue[object] = asyncio.Queue()
        client = DashboardClient.in_process(queue)
        dashboard = Dashboard(client, layout)
        try:
            assert len(dashboard.panels) == len(layout.panels)
            # Each Axes is attached to the dashboard's figure.
            for panel in dashboard.panels:
                assert panel.ax.figure is dashboard.figure
        finally:
            plt.close(dashboard.figure)


def test_dashboard_run_consumes_queue_and_updates(
    sample_fix_event: FixEvent,
    sample_bearing_report: BearingReport,
    sample_node_status: NodeStatus,
) -> None:
    """run() consumes a small batch of messages, dispatches, and exits cleanly."""

    async def run() -> Dashboard:
        queue: asyncio.Queue[object] = asyncio.Queue()
        # 5 queued messages: bearing, status, status, fix, fix.
        await queue.put(sample_bearing_report)
        await queue.put(sample_node_status)
        await queue.put(sample_node_status)
        await queue.put(sample_fix_event)
        await queue.put(sample_fix_event)
        await queue.put(None)  # sentinel
        client = DashboardClient.in_process(queue)
        dashboard = Dashboard(client, DEMO_LAYOUT_DEBUG)
        await dashboard.run()
        return dashboard

    dashboard = asyncio.run(run())
    # FixPanel must have current_fix set; ResidualsPanel must too.
    fix_panels = [p for p in dashboard.panels if type(p).__name__ == "FixPanel"]
    assert fix_panels
    assert fix_panels[0]._current_fix is not None
    plt.close(dashboard.figure)


def test_dashboard_run_exits_cleanly_on_cancel(sample_fix_event: FixEvent) -> None:
    """A long-running dashboard cancels cleanly without hanging tasks."""

    async def run() -> None:
        queue: asyncio.Queue[object] = asyncio.Queue()
        await queue.put(sample_fix_event)
        # Do NOT put a sentinel; the dashboard will block on the next
        # get() until we cancel it.
        client = DashboardClient.in_process(queue)
        dashboard = Dashboard(client, DEMO_LAYOUT_MINIMAL)
        task = asyncio.create_task(dashboard.run())
        # Yield once so the dashboard processes the queued message.
        await asyncio.sleep(_CANCEL_DELAY_S)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        plt.close(dashboard.figure)

    asyncio.run(run())


def test_dashboard_uses_agg_backend_under_tests() -> None:
    """The conftest forces Agg backend; verify it stuck."""
    # matplotlib's get_backend() returns the active backend string.
    # conftest.py sets it to "Agg" before any panel import.
    backend = matplotlib.get_backend().lower()
    assert "agg" in backend, f"expected Agg backend; got {backend}"


def test_dashboard_savefig_produces_png(sample_fix_event: FixEvent) -> None:
    """savefig() writes a PNG -- the Phase C bench-side artefact path."""

    async def run() -> Path:
        queue: asyncio.Queue[object] = asyncio.Queue()
        await queue.put(sample_fix_event)
        await queue.put(None)
        client = DashboardClient.in_process(queue)
        dashboard = Dashboard(client, DEMO_LAYOUT_MINIMAL)
        await dashboard.run()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = Path(tmp.name)
        dashboard.savefig(path)
        plt.close(dashboard.figure)
        return path

    out_path = asyncio.run(run())
    try:
        assert out_path.exists()
        assert out_path.stat().st_size > 0
    finally:
        if out_path.exists():
            out_path.unlink()
