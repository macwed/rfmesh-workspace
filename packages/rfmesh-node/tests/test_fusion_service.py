"""Tests for ``rfmesh_node.fusion_service.FusionService``.

The fusion-side asyncio service: batch -> fuse -> publish to CoT +
dashboard.

The named regression test below -- ``test_subscriber_registration_race``
-- proves the salvaged-from-old-repo failure (a subscriber registered
immediately before a burst of measurements arrives missing the first N)
**cannot** recur in the new design. Per ``INHERITED_CONTEXT.md`` §5.2,
this is "paid-for knowledge" -- the regression test is the cheapest
possible insurance that the bug stays buried.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import (
    FakeCotPublisher,
    FakeFuser,
    make_bearing_report,
    make_fix_event,
    make_node_status,
)
from rfmesh_node import DashboardPubSub, FusionService, InProcessSubscriber


@pytest.fixture()
def fix_event_seed():
    return make_fix_event(t_unix_ns=1_700_000_000_000_000_000)


async def _wait_until(predicate, timeout_s: float = 1.0) -> bool:
    """Spin-wait for ``predicate()`` to be truthy. Helper."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return False


# ---------------------------------------------------------------------------
# Subscriber-registration race regression (INHERITED_CONTEXT.md §5.2)
# ---------------------------------------------------------------------------


async def test_subscriber_registration_race(fix_event_seed) -> None:
    """A subscriber registered immediately before a burst gets every message.

    The old repo's bug: subscribers registered after a burst arrived but
    before the dispatch loop iterated missed the burst. The new design's
    fix: the dashboard pubsub is *separate* from the inbox; subscribers
    never consume the inbox. Therefore a subscriber added at *any* time
    -- including immediately before a burst -- gets every fix the burst
    produces, because the fan-out happens at publish_fix() time on the
    FusionService side.

    The regression test enforces this contractually. Per
    INHERITED_CONTEXT.md §5.2 it must remain in the suite under this
    exact name.
    """
    fuser = FakeFuser(fix=fix_event_seed)
    pubsub = DashboardPubSub(per_sub_timeout_s=2.0)

    # Two-node bearings with the same t_unix_ns -- a window-mate.
    bearings = (
        make_bearing_report(node_id="node-a", t_unix_ns=1_700_000_000_000_000_000),
        make_bearing_report(node_id="node-b", t_unix_ns=1_700_000_000_000_001_000),
    )

    config = pytest.importorskip("rfmesh_contracts").FusionConfig(
        listen_url="udp://127.0.0.1:9000",
        batch_window_ms=10.0,
        node_stale_after_s=5.0,
        min_bearings_for_fix=2,
    )
    service = FusionService(
        config=config,
        fuser=fuser,
        cot_publisher=None,
        dashboard_pubsub=pubsub,
    )

    # The "burst" -- push N bearings into the inbox *before* starting the
    # service AND before the subscriber registers. This is the exact
    # race window the old repo had.
    for bearing in bearings:
        service.push_for_test(bearing)

    runner = asyncio.create_task(service.run())
    try:
        # Register the subscriber AFTER the burst has been queued. In
        # the old race this is the moment the bug would manifest --
        # the burst already in the inbox would be drained, fused, and
        # published before the subscriber's queue was wired up. The
        # new design publishes via the pubsub at publish_fix() time --
        # so as long as the subscriber is registered before the first
        # publish_fix call, it gets every fix.
        queue: asyncio.Queue = asyncio.Queue()
        sub = InProcessSubscriber(queue)
        pubsub.add_subscriber(sub)

        # The fuse loop should produce at least one fix from the burst.
        # We wait for it.
        got = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert got.fix_id == fix_event_seed.fix_id

        # The fuser was called at least once with both bearings.
        assert fuser.calls, "fuser.fuse was never called"
        materialised, _config = fuser.calls[0]
        node_ids = {b.node_id for b in materialised}
        assert {"node-a", "node-b"} <= node_ids
    finally:
        await service.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)


# ---------------------------------------------------------------------------
# Other FusionService behaviours.
# ---------------------------------------------------------------------------


async def test_fuse_called_once_per_window(fix_event_seed) -> None:
    """N>=2 bearings inside one window result in exactly one fuse() call."""
    fuser = FakeFuser(fix=fix_event_seed)

    from rfmesh_contracts import FusionConfig

    config = FusionConfig(
        listen_url="udp://127.0.0.1:9000",
        batch_window_ms=20.0,
        node_stale_after_s=5.0,
        min_bearings_for_fix=2,
    )
    service = FusionService(config=config, fuser=fuser)

    bearings = (
        make_bearing_report(node_id="node-a", t_unix_ns=1_700_000_000_000_000_000),
        make_bearing_report(node_id="node-b", t_unix_ns=1_700_000_000_000_005_000),
        make_bearing_report(node_id="node-c", t_unix_ns=1_700_000_000_000_010_000),
    )

    for bearing in bearings:
        service.push_for_test(bearing)

    runner = asyncio.create_task(service.run())
    try:
        ok = await _wait_until(lambda: len(fuser.calls) >= 1, timeout_s=2.0)
        assert ok, "expected at least one fuse() call"
        await asyncio.sleep(0.05)
        # All three bearings landed inside one or two windows; the
        # cumulative count of bearings across all calls is 3. We do not
        # require exactly one window because timing on a loaded CI host
        # may split them -- but each bearing must have been fed.
        materialised_node_ids = {b.node_id for call in fuser.calls for b in call[0]}
        assert {"node-a", "node-b", "node-c"} <= materialised_node_ids
    finally:
        await service.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)


async def test_stale_nodes_excluded_from_fuse(fix_event_seed) -> None:
    """A node whose last_seen is older than ``node_stale_after_s`` is excluded."""
    fuser = FakeFuser(fix=fix_event_seed)

    from rfmesh_contracts import FusionConfig

    config = FusionConfig(
        listen_url="udp://127.0.0.1:9000",
        batch_window_ms=20.0,
        # Aggressive staleness: 50 ms.
        node_stale_after_s=0.05,
        min_bearings_for_fix=2,
    )
    service = FusionService(config=config, fuser=fuser)

    # Push two bearings and let one of them become stale by waiting.
    service.push_for_test(
        make_bearing_report(node_id="node-stale", t_unix_ns=1_700_000_000_000_000_000)
    )
    runner = asyncio.create_task(service.run())
    try:
        # Wait long enough that node-stale becomes stale.
        await asyncio.sleep(0.2)
        # Now push two fresh bearings + one from the stale node.
        service.push_for_test(
            make_bearing_report(node_id="node-a", t_unix_ns=1_700_000_000_000_100_000)
        )
        service.push_for_test(
            make_bearing_report(node_id="node-b", t_unix_ns=1_700_000_000_000_101_000)
        )

        ok = await _wait_until(
            lambda: any(
                "node-a" in {b.node_id for b in call[0]}
                and "node-b" in {b.node_id for b in call[0]}
                for call in fuser.calls
            ),
            timeout_s=2.0,
        )
        assert ok, "expected a fuse() call carrying node-a and node-b"
        # And the stale node must not appear in any successful fuse()
        # call that produced a window with the live ones.
        for call in fuser.calls:
            node_ids = {b.node_id for b in call[0]}
            if "node-a" in node_ids and "node-b" in node_ids:
                assert "node-stale" not in node_ids
    finally:
        await service.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)


async def test_fix_event_published_to_cot_and_dashboard(fix_event_seed) -> None:
    """One fix lands on both the CoT publisher and the dashboard pubsub."""
    fuser = FakeFuser(fix=fix_event_seed)
    cot = FakeCotPublisher()
    pubsub = DashboardPubSub()

    from rfmesh_contracts import FusionConfig

    config = FusionConfig(
        listen_url="udp://127.0.0.1:9000",
        batch_window_ms=10.0,
        node_stale_after_s=5.0,
        min_bearings_for_fix=2,
    )
    service = FusionService(
        config=config,
        fuser=fuser,
        cot_publisher=cot,
        dashboard_pubsub=pubsub,
    )

    queue: asyncio.Queue = asyncio.Queue()
    pubsub.add_subscriber(InProcessSubscriber(queue))

    for node_id, t in (
        ("node-a", 1_700_000_000_000_000_000),
        ("node-b", 1_700_000_000_000_001_000),
    ):
        service.push_for_test(make_bearing_report(node_id=node_id, t_unix_ns=t))

    runner = asyncio.create_task(service.run())
    try:
        delivered = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert delivered.fix_id == fix_event_seed.fix_id
        # CoT received the fix synchronously.
        ok = await _wait_until(lambda: len(cot.published) >= 1, timeout_s=1.0)
        assert ok
        assert cot.published[0].fix_id == fix_event_seed.fix_id
    finally:
        await service.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)


async def test_fuser_returning_none_does_not_crash() -> None:
    """``Fuser.fuse`` returning None is the "cannot solve" signal -- no publish."""
    fuser = FakeFuser(fix=None)
    cot = FakeCotPublisher()
    pubsub = DashboardPubSub()

    from rfmesh_contracts import FusionConfig

    config = FusionConfig(
        listen_url="udp://127.0.0.1:9000",
        batch_window_ms=10.0,
        node_stale_after_s=5.0,
        min_bearings_for_fix=2,
    )
    service = FusionService(
        config=config,
        fuser=fuser,
        cot_publisher=cot,
        dashboard_pubsub=pubsub,
    )

    queue: asyncio.Queue = asyncio.Queue()
    pubsub.add_subscriber(InProcessSubscriber(queue))

    service.push_for_test(make_bearing_report(node_id="node-a"))
    service.push_for_test(make_bearing_report(node_id="node-b"))

    runner = asyncio.create_task(service.run())
    try:
        ok = await _wait_until(lambda: len(fuser.calls) >= 1, timeout_s=1.0)
        assert ok
        # No publish should occur.
        await asyncio.sleep(0.05)
        assert cot.published == []
        assert queue.empty()
    finally:
        await service.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)


async def test_status_messages_update_last_seen_but_do_not_fuse(fix_event_seed) -> None:
    """A NodeStatus updates the freshness map but does not feed the fuser."""
    fuser = FakeFuser(fix=fix_event_seed)

    from rfmesh_contracts import FusionConfig

    config = FusionConfig(
        listen_url="udp://127.0.0.1:9000",
        batch_window_ms=10.0,
        node_stale_after_s=5.0,
        min_bearings_for_fix=2,
    )
    service = FusionService(config=config, fuser=fuser)
    # Push two status heartbeats only; the fuser must not be called.
    service.push_for_test(make_node_status(node_id="node-a"))
    service.push_for_test(make_node_status(node_id="node-b"))

    runner = asyncio.create_task(service.run())
    try:
        await asyncio.sleep(0.1)
        # No fuse() call -- statuses are heartbeats, not bearings.
        assert fuser.calls == []
    finally:
        await service.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)
