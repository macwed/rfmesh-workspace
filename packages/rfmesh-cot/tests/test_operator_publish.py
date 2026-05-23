"""Integration tests for operator-marker publishing over the loopback TAK.

Uses the ``loopback_tcp_server`` fixture (conftest) to assert that
``publish_marker`` / ``delete_marker`` actually put CoT bytes on the wire,
both from the publisher's own loop and -- the thread-safety claim -- from
a separate operator thread via ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio

import pytest
from rfmesh_cot import OperatorMarker, PyTAKCotPublisher

# loopback_tcp_server is a pytest_asyncio fixture from conftest.py.


@pytest.mark.asyncio
async def test_publish_marker_on_loop_reaches_server(loopback_tcp_server) -> None:
    async with PyTAKCotPublisher(loopback_tcp_server.url) as pub:
        pub.publish_marker(
            OperatorMarker(
                template_key="hostile",
                uid="rfmesh.op.hostile.x",
                lat_deg=50.0,
                lon_deg=4.0,
                callsign="X",
            )
        )
        await asyncio.sleep(0.1)
    blob = bytes(loopback_tcp_server.received)
    assert b'type="a-h-G"' in blob
    assert b'uid="rfmesh.op.hostile.x"' in blob


@pytest.mark.asyncio
async def test_delete_marker_reaches_server(loopback_tcp_server) -> None:
    async with PyTAKCotPublisher(loopback_tcp_server.url) as pub:
        pub.delete_marker("rfmesh.op.hostile.x")
        await asyncio.sleep(0.1)
    blob = bytes(loopback_tcp_server.received)
    assert b'type="t-x-d-d"' in blob
    assert b'uid="rfmesh.op.hostile.x"' in blob


@pytest.mark.asyncio
async def test_publish_marker_from_worker_thread(loopback_tcp_server) -> None:
    """The thread-safety claim: an off-loop thread can publish safely."""
    async with PyTAKCotPublisher(loopback_tcp_server.url) as pub:
        marker = OperatorMarker(
            template_key="waypoint",
            uid="rfmesh.op.waypoint.wp1",
            lat_deg=51.0,
            lon_deg=3.0,
        )
        # Publish from a thread that is NOT running the publisher's loop.
        await asyncio.to_thread(pub.publish_marker, marker)
        await asyncio.sleep(0.15)
    blob = bytes(loopback_tcp_server.received)
    assert b'uid="rfmesh.op.waypoint.wp1"' in blob
    assert b'type="b-m-p-w"' in blob
