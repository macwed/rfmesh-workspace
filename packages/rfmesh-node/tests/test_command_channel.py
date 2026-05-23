"""Unit tests for ``rfmesh_node.command_channel``.

Hardware-free: stands up a real aiohttp WS server in the test and
points the channel at it. Covers:

* Happy path: a frame sent server-side is parsed + dispatched.
* Bad frames are dropped + logged, channel stays open.
* Handler exceptions don't tear the channel down.
* ``stopping`` event terminates the run loop cleanly.
* Reconnect-on-disconnect: server hangs up, channel reconnects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket

import pytest
from aiohttp import web
from rfmesh_node.command_channel import CommandChannel
from rfmesh_node.commands import AllStopCommand, ManualSteerCommand


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


async def _start_server() -> tuple[web.AppRunner, int, asyncio.Queue[web.WebSocketResponse]]:
    """Start an aiohttp WS server exposing /ws/node/{node_id}.

    Returns (runner, port, accepted_ws_queue). The caller awaits
    ``runner.cleanup()`` to tear down.
    """
    connections: asyncio.Queue[web.WebSocketResponse] = asyncio.Queue()

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await connections.put(ws)
        # Iterate received frames so the server stays alive; the
        # client (CommandChannel) doesn't send anything here, so the
        # loop just blocks waiting for the close frame.
        async for _msg in ws:
            pass
        return ws

    app = web.Application()
    app.add_routes([web.get("/ws/node/{node_id}", ws_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner, port, connections


@pytest.mark.asyncio
async def test_channel_dispatches_manual_steer() -> None:
    runner, port, connections = await _start_server()
    received: list[ManualSteerCommand | AllStopCommand] = []
    stopping = asyncio.Event()

    async def handler(cmd: ManualSteerCommand | AllStopCommand) -> None:
        received.append(cmd)
        if isinstance(cmd, ManualSteerCommand) and cmd.target_angle_deg == 99.0:
            stopping.set()

    channel = CommandChannel(
        backend_ws_url=f"ws://127.0.0.1:{port}",
        node_id="node-a",
        handler=handler,
    )
    task = asyncio.create_task(channel.run(stopping))

    server_ws = await asyncio.wait_for(connections.get(), timeout=2.0)
    await server_ws.send_str(
        json.dumps(
            {
                "kind": "manual_steer",
                "axis": 0,
                "target_angle_deg": 99.0,
                "requestor_id": "test",
            }
        )
    )
    try:
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        await runner.cleanup()

    assert len(received) == 1
    cmd = received[0]
    assert isinstance(cmd, ManualSteerCommand)
    assert cmd.target_angle_deg == 99.0


@pytest.mark.asyncio
async def test_channel_drops_bad_frame_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner, port, connections = await _start_server()
    received: list[ManualSteerCommand | AllStopCommand] = []
    stopping = asyncio.Event()

    async def handler(cmd: ManualSteerCommand | AllStopCommand) -> None:
        received.append(cmd)
        stopping.set()

    channel = CommandChannel(
        backend_ws_url=f"ws://127.0.0.1:{port}",
        node_id="node-b",
        handler=handler,
    )
    task = asyncio.create_task(channel.run(stopping))
    server_ws = await asyncio.wait_for(connections.get(), timeout=2.0)
    # 1: bad JSON.
    with caplog.at_level(logging.WARNING):
        await server_ws.send_str("this is not json")
        # 2: schema-invalid.
        await server_ws.send_str(json.dumps({"kind": "wibble"}))
        # Small breathing room so the dispatcher consumes both.
        await asyncio.sleep(0.05)
    # 3: a good frame ends the test.
    await server_ws.send_str(
        json.dumps({"kind": "manual_steer", "axis": 0, "target_angle_deg": 0.0})
    )
    try:
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        await runner.cleanup()
    assert len(received) == 1
    assert any("dropping bad frame" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_handler_exception_does_not_close_channel() -> None:
    """A handler that raises must not tear down the channel."""
    runner, port, connections = await _start_server()
    delivered: list[str] = []
    stopping = asyncio.Event()

    async def handler(cmd: ManualSteerCommand | AllStopCommand) -> None:
        if not delivered:
            delivered.append("first")
            raise RuntimeError("boom")
        delivered.append("second")
        stopping.set()

    channel = CommandChannel(
        backend_ws_url=f"ws://127.0.0.1:{port}",
        node_id="node-c",
        handler=handler,
    )
    task = asyncio.create_task(channel.run(stopping))
    server_ws = await asyncio.wait_for(connections.get(), timeout=2.0)
    await server_ws.send_str(
        json.dumps({"kind": "manual_steer", "axis": 0, "target_angle_deg": 1.0})
    )
    await server_ws.send_str(
        json.dumps({"kind": "manual_steer", "axis": 0, "target_angle_deg": 2.0})
    )
    try:
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        await runner.cleanup()
    assert delivered == ["first", "second"]


@pytest.mark.asyncio
async def test_stop_event_ends_run_loop() -> None:
    runner, port, connections = await _start_server()
    stopping = asyncio.Event()

    async def handler(cmd: ManualSteerCommand | AllStopCommand) -> None:
        del cmd

    channel = CommandChannel(
        backend_ws_url=f"ws://127.0.0.1:{port}",
        node_id="node-d",
        handler=handler,
    )
    task = asyncio.create_task(channel.run(stopping))
    await asyncio.wait_for(connections.get(), timeout=2.0)
    stopping.set()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_reconnect_after_server_close() -> None:
    """First connection is closed by server; channel reconnects + dispatches."""
    runner, port, connections = await _start_server()
    received: list[ManualSteerCommand | AllStopCommand] = []
    stopping = asyncio.Event()

    async def handler(cmd: ManualSteerCommand | AllStopCommand) -> None:
        received.append(cmd)
        stopping.set()

    channel = CommandChannel(
        backend_ws_url=f"ws://127.0.0.1:{port}",
        node_id="node-e",
        handler=handler,
    )
    task = asyncio.create_task(channel.run(stopping))
    first_ws = await asyncio.wait_for(connections.get(), timeout=2.0)
    await first_ws.close()  # forcibly close — channel must reconnect
    second_ws = await asyncio.wait_for(connections.get(), timeout=5.0)
    await second_ws.send_str(
        json.dumps({"kind": "manual_steer", "axis": 0, "target_angle_deg": 7.0})
    )
    try:
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        await runner.cleanup()
    assert len(received) == 1
    cmd = received[0]
    assert isinstance(cmd, ManualSteerCommand)
    assert cmd.target_angle_deg == 7.0
