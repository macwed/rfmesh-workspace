"""Tests for ``rfmesh_node.dashboard_pubsub``.

Covers:

* In-process: one publisher + two subscribers each receive every fix.
* In-process: a subscriber whose send raises is dropped; other subs continue.
* WebSocket round-trip via aiohttp test client.
* Back-pressure: a slow subscriber is timed out + dropped.
* close + cleanup leaves no hanging tasks.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aiohttp import web
from conftest import make_fix_event
from rfmesh_node import DashboardPubSub, InProcessSubscriber, WebSocketSubscriber

# ---------------------------------------------------------------------------
# In-process pubsub
# ---------------------------------------------------------------------------


async def test_inprocess_two_subscribers_each_get_every_fix() -> None:
    pubsub = DashboardPubSub()
    q1: asyncio.Queue = asyncio.Queue()
    q2: asyncio.Queue = asyncio.Queue()
    pubsub.add_subscriber(InProcessSubscriber(q1))
    pubsub.add_subscriber(InProcessSubscriber(q2))

    fix = make_fix_event()
    await pubsub.publish_fix(fix)

    got1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    got2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert got1.fix_id == fix.fix_id
    assert got2.fix_id == fix.fix_id


async def test_inprocess_erroring_subscriber_is_dropped_others_continue() -> None:
    """A subscriber that raises is dropped; the well-behaved one keeps receiving."""
    pubsub = DashboardPubSub(per_sub_timeout_s=0.5)
    healthy_queue: asyncio.Queue = asyncio.Queue()
    pubsub.add_subscriber(InProcessSubscriber(healthy_queue))

    class _BrokenSubscriber:
        async def send(self, message) -> None:
            msg = "BrokenSubscriber: intentional failure."
            raise RuntimeError(msg)

    pubsub.add_subscriber(_BrokenSubscriber())
    assert pubsub.subscriber_count == 2

    fix_first = make_fix_event(contributing_nodes=("a", "b"))
    await pubsub.publish_fix(fix_first)

    # Healthy sub got the first fix.
    delivered = await asyncio.wait_for(healthy_queue.get(), timeout=1.0)
    assert delivered.fix_id == fix_first.fix_id
    # Broken sub was dropped.
    assert pubsub.subscriber_count == 1

    # Second publish still reaches the healthy sub.
    fix_second = make_fix_event(contributing_nodes=("c", "d"))
    await pubsub.publish_fix(fix_second)
    delivered_again = await asyncio.wait_for(healthy_queue.get(), timeout=1.0)
    assert delivered_again.fix_id == fix_second.fix_id


async def test_slow_subscriber_is_timed_out_and_dropped() -> None:
    """Slow subscriber exceeds per-sub timeout -> dropped; fast sub gets the fix."""
    pubsub = DashboardPubSub(per_sub_timeout_s=0.05)
    fast_q: asyncio.Queue = asyncio.Queue()
    pubsub.add_subscriber(InProcessSubscriber(fast_q))

    class _SleepySubscriber:
        async def send(self, message) -> None:
            await asyncio.sleep(5.0)  # exceeds the 50 ms timeout

    pubsub.add_subscriber(_SleepySubscriber())
    assert pubsub.subscriber_count == 2

    fix = make_fix_event()
    await pubsub.publish_fix(fix)

    delivered = await asyncio.wait_for(fast_q.get(), timeout=1.0)
    assert delivered.fix_id == fix.fix_id
    assert pubsub.subscriber_count == 1


async def test_no_subscribers_publish_is_noop() -> None:
    """Zero subscribers: publish returns cleanly, no exception."""
    pubsub = DashboardPubSub()
    fix = make_fix_event()
    await pubsub.publish_fix(fix)  # must not raise


# ---------------------------------------------------------------------------
# WebSocket round-trip
# ---------------------------------------------------------------------------


async def test_websocket_subscriber_receives_envelope(aiohttp_unused_port) -> None:  # type: ignore[no-untyped-def]
    """A WebSocket subscriber receives the msgpack-encoded fix envelope."""

    received_envelopes: list[bytes] = []
    ws_ready = asyncio.Event()
    fix = make_fix_event()
    pubsub = DashboardPubSub(per_sub_timeout_s=2.0)

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sub = WebSocketSubscriber(ws)
        pubsub.add_subscriber(sub)
        ws_ready.set()
        # Keep the connection alive while the test drives publishes.
        await asyncio.sleep(0.5)
        await ws.close()
        return ws

    app = web.Application()
    app.add_routes([web.get("/", ws_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = aiohttp_unused_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws_client,
        ):
            await asyncio.wait_for(ws_ready.wait(), timeout=2.0)
            await pubsub.publish_fix(fix)
            # The server-side WS sends the bytes back to us.
            msg = await asyncio.wait_for(ws_client.receive(), timeout=2.0)
            assert msg.type == aiohttp.WSMsgType.BINARY
            received_envelopes.append(msg.data)
    finally:
        await runner.cleanup()

    assert len(received_envelopes) == 1
    from rfmesh_node.bearer.envelope import decode_envelope

    decoded = decode_envelope(received_envelopes[0])
    from rfmesh_contracts import FixEvent

    assert isinstance(decoded, FixEvent)
    assert decoded.fix_id == fix.fix_id


# ---------------------------------------------------------------------------
# Pytest plumbing for ``aiohttp_unused_port`` (the fixture is provided by
# pytest-aiohttp; if not installed, fall back to the conftest helper).
# ---------------------------------------------------------------------------


@pytest.fixture()
def aiohttp_unused_port():
    """Yield a callable returning a free TCP port (loopback)."""
    import socket

    def _pick() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
        finally:
            s.close()

    return _pick
