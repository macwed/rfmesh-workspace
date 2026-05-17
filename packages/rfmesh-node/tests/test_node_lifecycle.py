"""Tests for ``rfmesh_node.node.Node`` lifecycle.

Covers:

* run() + shutdown() releases receiver + bearer, no hanging tasks.
* Heartbeat emits at the configured interval.
* Capability mismatch at startup raises before the run loop starts.
* A node with a SyntheticReceiver produces bearings consumed by a fake Bearer.
* Shutdown propagates to inner tasks.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import make_bearing_report
from rfmesh_contracts import (
    BearerConfig,
    BearerKind,
    BearingReport,
    Capability,
    GeodeticPosition,
    NodeConfig,
    NodeStatus,
    SDRConfig,
)
from rfmesh_node import CapabilityMismatchError, Node

# ---------------------------------------------------------------------------
# Test fakes -- minimal Receiver + Bearer impls.
# ---------------------------------------------------------------------------


class _FakeCaps:
    driver = "sim"
    n_coherent_channels = 1
    actual_sample_rate_hz = 2_400_000.0
    is_power_calibrated = False


class _FakeReceiver:
    """Records calls; no actual IQ generation."""

    def __init__(self) -> None:
        self.open_called = 0
        self.configure_called = 0
        self.close_called = 0

    def open(self) -> None:
        self.open_called += 1

    def configure(self, config: object) -> None:
        self.configure_called += 1

    def read(self, n_samples: int) -> Any:
        raise NotImplementedError

    def capabilities(self) -> _FakeCaps:
        return _FakeCaps()

    def close(self) -> None:
        self.close_called += 1


class _FakeBearer:
    def __init__(self) -> None:
        self.statuses: list[NodeStatus] = []
        self.bearings: list[BearingReport] = []
        self.closed = False

    def send_bearing(self, report: BearingReport) -> None:
        self.bearings.append(report)

    def send_status(self, status: NodeStatus) -> None:
        self.statuses.append(status)

    def receive(self) -> tuple:
        return ()

    def close(self) -> None:
        self.closed = True


def _node_pos() -> GeodeticPosition:
    return GeodeticPosition(lat_deg=50.85, lon_deg=4.35, hae_m=50.0, sigma_m=5.0)


def _l1_config(
    *, heartbeat_interval_s: float = 0.05, capabilities=(Capability.L1_RSSI,)
) -> NodeConfig:
    return NodeConfig(
        node_id="node-test",
        position=_node_pos(),
        heading_deg=0.0,
        sdr=SDRConfig(
            driver="sim",
            sample_rate_hz=2_400_000.0,
            center_freq_hz=915_000_000.0,
            gain_db=30.0,
        ),
        array=None,
        capabilities=capabilities,
        bearer=BearerConfig(
            kind=BearerKind.WIFI,
            heartbeat_interval_s=heartbeat_interval_s,
        ),
        fusion_endpoint="udp://127.0.0.1:9000",
    )


# ---------------------------------------------------------------------------
# Lifecycle.
# ---------------------------------------------------------------------------


async def test_run_then_shutdown_releases_resources() -> None:
    receiver = _FakeReceiver()
    bearer = _FakeBearer()
    config = _l1_config()
    node = Node(config, receiver=receiver, bearer=bearer)

    runner = asyncio.create_task(node.run())
    # Give the runtime a moment to come up.
    await asyncio.sleep(0.05)
    await node.shutdown()
    await asyncio.wait_for(runner, timeout=2.0)

    assert receiver.open_called == 1
    assert receiver.configure_called == 1
    assert receiver.close_called >= 1
    assert bearer.closed is True
    # No hanging tasks beyond the ones pytest-asyncio owns.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    # The pytest-asyncio plugin's own scheduling task may be present;
    # what we are guarding against is any *Node* task leaking. We do
    # this by name.
    assert not any((t.get_name() or "").startswith("node-") for t in pending)


async def test_heartbeat_emits_at_configured_interval() -> None:
    receiver = _FakeReceiver()
    bearer = _FakeBearer()
    config = _l1_config(heartbeat_interval_s=0.05)
    node = Node(config, receiver=receiver, bearer=bearer)

    runner = asyncio.create_task(node.run())
    try:
        # Wait long enough that 3-5 heartbeats have fired.
        await asyncio.sleep(0.25)
        # We expect at least 3 heartbeats and not many more than 6.
        assert len(bearer.statuses) >= 3
        assert len(bearer.statuses) <= 8
        # Every heartbeat carries the active capabilities.
        for status in bearer.statuses:
            assert status.node_id == "node-test"
            assert Capability.L1_RSSI in status.active_capabilities
    finally:
        await node.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)


async def test_capability_mismatch_raises_during_run() -> None:
    """Declared L2 without coherent receiver -> raise inside ``run``.

    NodeConfig's own validator forbids declaring L2 without an array,
    so the next-level mismatch we can test is L1 declared but the
    receiver pretends to be L1-incapable. We do this by giving the
    receiver zero coherent channels and declaring an L1 + L2 mix
    that the config does not validate against -- actually the cleanest
    way to test this is to declare L2 + a non-coherent receiver and
    verify ``run`` raises before the heartbeat starts.
    """
    receiver = _FakeReceiver()  # not a CoherentReceiver
    bearer = _FakeBearer()
    # Build an L2 config: requires array.
    config = NodeConfig(
        node_id="node-mismatch",
        position=_node_pos(),
        heading_deg=0.0,
        sdr=SDRConfig(
            driver="bladerf",
            sample_rate_hz=2_400_000.0,
            center_freq_hz=915_000_000.0,
            gain_db=30.0,
        ),
        array=__import__("rfmesh_contracts").ArrayConfig(
            geometry=__import__("rfmesh_contracts").ArrayGeometry.ULA,
            n_elements=2,
            element_spacing_m=0.164,
        ),
        capabilities=(Capability.L2_MUSIC,),
        bearer=BearerConfig(kind=BearerKind.WIFI, heartbeat_interval_s=0.05),
        fusion_endpoint="udp://127.0.0.1:9000",
    )
    node = Node(config, receiver=receiver, bearer=bearer)
    with pytest.raises(CapabilityMismatchError):
        await node.run()
    # Receiver was opened + closed even though the run failed.
    assert receiver.open_called == 1
    assert receiver.close_called >= 1


async def test_emit_for_test_routes_through_bearer() -> None:
    """The test affordance ``emit_for_test`` ships a bearing via the bearer."""
    receiver = _FakeReceiver()
    bearer = _FakeBearer()
    config = _l1_config()
    node = Node(config, receiver=receiver, bearer=bearer)

    runner = asyncio.create_task(node.run())
    try:
        await asyncio.sleep(0.05)
        report = make_bearing_report(node_id="node-test")
        await node.emit_for_test(report)
        assert bearer.bearings[-1].node_id == "node-test"
    finally:
        await node.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)


async def test_heartbeat_surfaces_bearer_health_summary() -> None:
    """``NodeStatus.status_detail`` carries ``bearer.health_summary()`` when present.

    Regression test for the architect-council BLOCK on commit f8b2a48
    (B3: BothBearer's LoRa-down state must surface to the heartbeat
    channel, not be hidden behind ``status_detail=""``).

    Uses an inline stub bearer whose ``health_summary`` returns the
    canonical ``"LoRa bearer down, Wi-Fi only"`` string. The heartbeat
    output must contain that exact text.
    """

    class _StubBearerWithHealth(_FakeBearer):
        def health_summary(self) -> str:
            return "LoRa bearer down, Wi-Fi only"

    receiver = _FakeReceiver()
    bearer = _StubBearerWithHealth()
    config = _l1_config(heartbeat_interval_s=0.05)
    node = Node(config, receiver=receiver, bearer=bearer)

    runner = asyncio.create_task(node.run())
    try:
        # Give the runtime time to emit at least one heartbeat.
        await asyncio.sleep(0.2)
    finally:
        await node.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)

    assert bearer.statuses, "expected at least one heartbeat"
    for status in bearer.statuses:
        assert status.status_detail == "LoRa bearer down, Wi-Fi only"


async def test_heartbeat_status_detail_empty_when_bearer_has_no_health_summary() -> None:
    """Bearers without ``health_summary`` produce ``status_detail=""`` (back-compat)."""
    receiver = _FakeReceiver()
    bearer = _FakeBearer()  # no health_summary method
    config = _l1_config(heartbeat_interval_s=0.05)
    node = Node(config, receiver=receiver, bearer=bearer)

    runner = asyncio.create_task(node.run())
    try:
        await asyncio.sleep(0.15)
    finally:
        await node.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)

    assert bearer.statuses
    for status in bearer.statuses:
        assert status.status_detail == ""


async def test_run_called_twice_raises() -> None:
    """Calling run() twice on the same Node is a programming error."""
    receiver = _FakeReceiver()
    bearer = _FakeBearer()
    config = _l1_config()
    node = Node(config, receiver=receiver, bearer=bearer)

    runner = asyncio.create_task(node.run())
    try:
        await asyncio.sleep(0.05)
        with pytest.raises(RuntimeError, match="run called twice"):
            await node.run()
    finally:
        await node.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)
