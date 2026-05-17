"""Tests for rfmesh_ops.client -- DashboardClient transport factories."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from aiohttp import web
from rfmesh_contracts import (
    BearingReport,
    Capability,
    ConfidenceLevel,
    FixEvent,
    GeodeticPosition,
)
from rfmesh_ops.client import DashboardClient

_EXPECTED_COUNT = 1


def test_in_process_round_trip_one_fix(sample_fix_event: FixEvent) -> None:
    """A FixEvent put on the queue is yielded by stream()."""

    async def run() -> list[object]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        await queue.put(sample_fix_event)
        await queue.put(None)  # sentinel: end-of-stream
        client = DashboardClient.in_process(queue)
        collected: list[object] = []
        async for msg in client.stream():
            collected.append(msg)
        return collected

    received = asyncio.run(run())
    assert len(received) == 1
    assert isinstance(received[0], FixEvent)
    assert received[0].fix_id == sample_fix_event.fix_id


def test_in_process_decodes_envelope_dicts(
    sample_geodetic_position: GeodeticPosition,
) -> None:
    """The in_process queue also accepts ``{type, payload}`` envelopes."""

    async def run() -> list[object]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        report_payload = {
            "node_id": "node-rtl-01",
            "t_unix_ns": 1_700_000_000_000_000_000,
            "node_position": sample_geodetic_position.model_dump(),
            "azimuth_deg": 90.0,
            "azimuth_sigma_deg": 4.0,
            "method": Capability.L1_RSSI.value,
        }
        await queue.put({"type": "bearing_report", "payload": report_payload})
        await queue.put(None)
        client = DashboardClient.in_process(queue)
        collected: list[object] = []
        async for msg in client.stream():
            collected.append(msg)
        return collected

    received = asyncio.run(run())
    assert len(received) == 1
    assert isinstance(received[0], BearingReport)
    assert received[0].azimuth_deg == pytest.approx(90.0)


def test_stream_is_async_iterator(sample_fix_event: FixEvent) -> None:
    """stream() returns an AsyncIterator -- check the protocol shape."""

    async def run() -> bool:
        queue: asyncio.Queue[object] = asyncio.Queue()
        await queue.put(sample_fix_event)
        await queue.put(None)
        client = DashboardClient.in_process(queue)
        stream = client.stream()
        # AsyncIterator is duck-typed: __aiter__ + __anext__.
        return isinstance(stream, AsyncIterator)

    assert asyncio.run(run())


def test_websocket_round_trip_via_aiohttp_loopback() -> None:
    """A WebSocket loopback round-trip: server publishes one fix; client decodes it."""

    fix_payload = {
        "fix_id": "12345678-1234-5678-1234-567812345678",
        "t_unix_ns": 1_700_000_000_500_000_000,
        "position": {
            "lat_deg": 52.42,
            "lon_deg": 16.96,
            "hae_m": 0.0,
            "sigma_m": 120.0,
        },
        "covariance_m2": [22500.0, 0.0, 10000.0],
        "confidence_ellipse_95": {
            "semi_major_m": 393.0,
            "semi_minor_m": 357.0,
            "orientation_deg": 10.0,
        },
        "confidence_level": ConfidenceLevel.MEDIUM.value,
        "contributing_nodes": ["node-rtl-01", "node-rtl-02", "node-rtl-03"],
        "residuals_deg": [0.5, -0.3, 0.2],
        "gdop": 1.16,
        "method": "stansfield+mle",
    }

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        envelope = {"type": "fix_event", "payload": fix_payload}
        await ws.send_str(json.dumps(envelope))
        await ws.close()
        return ws

    async def run() -> tuple[list[object], str]:
        app = web.Application()
        app.router.add_get("/ws", ws_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server = next(iter(runner.sites))  # noqa: F841 -- documents lifecycle
        # Get the bound port from the TCPSite.
        host, port = site._server.sockets[0].getsockname()[:2]  # type: ignore[union-attr]
        url = f"ws://{host}:{port}/ws"
        client = DashboardClient.websocket(url)
        try:
            collected: list[object] = []
            async for msg in client.stream():
                collected.append(msg)
                if len(collected) >= 1:
                    break
        finally:
            await client.close()
            await runner.cleanup()
        return collected, url

    received, _url = asyncio.run(run())
    assert len(received) == 1
    assert isinstance(received[0], FixEvent)
    assert received[0].fix_id == UUID("12345678-1234-5678-1234-567812345678")


def test_close_is_idempotent_with_no_active_session() -> None:
    """close() before any connection is a no-op (no exception)."""

    async def run() -> None:
        client = DashboardClient.websocket("ws://localhost:1/ws")
        # No stream() iteration -> no session opened.
        await client.close()
        # Calling close again is also safe.
        await client.close()

    asyncio.run(run())


def test_unconfigured_client_raises_on_stream() -> None:
    """A client built without a factory raises RuntimeError on iteration."""

    async def run() -> None:
        client = DashboardClient()
        async for _ in client.stream():
            return

    with pytest.raises(RuntimeError, match="without a transport"):
        asyncio.run(run())
