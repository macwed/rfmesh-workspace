"""DashboardClient -- the transport-side consumer of FusionService.

Two factories that produce a uniform ``AsyncIterator`` of contract
messages (``BearingReport`` | ``FixEvent`` | ``NodeStatus``):

* ``DashboardClient.in_process(queue)`` -- consumes from an asyncio
  Queue. Used when the dashboard and the fusion-server share a host
  (BoTH3 deployment baseline).
* ``DashboardClient.websocket(url)`` -- consumes from a remote
  fusion-server via aiohttp's WebSocket client. Used for the remote
  dashboard case.

The wire format on the WebSocket is msgpack-wrapped contract dumps;
this client decodes ``{"type": "bearing_report" | "fix_event" |
"node_status", "payload": {...}}`` frames into the appropriate
Pydantic model. The frame envelope mirrors the
``rfmesh-node.dashboard_pubsub`` producer side
(``docs/design/ops-architecture.md`` §2.3).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
from rfmesh_contracts import BearingReport, FixEvent, NodeStatus

DashboardMessage = BearingReport | FixEvent | NodeStatus

# Wire envelope type tags. Stable; a change here is a coordination
# break with the rfmesh-node dashboard_pubsub producer side.
_TAG_BEARING = "bearing_report"
_TAG_FIX = "fix_event"
_TAG_STATUS = "node_status"


def _decode_envelope(envelope: dict[str, Any]) -> DashboardMessage | None:
    """Decode one wire envelope into a contract message.

    Returns ``None`` for envelopes with an unknown ``type`` tag --
    forward compatibility for future contract additions; the dashboard
    silently skips frames it does not recognise rather than crashing.
    """
    msg_type = envelope.get("type")
    payload = envelope.get("payload", {})
    if msg_type == _TAG_BEARING:
        return BearingReport(**payload)
    if msg_type == _TAG_FIX:
        return FixEvent(**payload)
    if msg_type == _TAG_STATUS:
        return NodeStatus(**payload)
    return None


class DashboardClient:
    """Transport-side consumer of FusionService.DashboardPubSub.

    Two factory constructors -- ``websocket(url)`` and
    ``in_process(queue)`` -- both produce an instance whose
    ``stream()`` is the same ``AsyncIterator`` shape, so the Dashboard
    does not branch on transport.
    """

    def __init__(self) -> None:
        # Use the factories. Direct construction is allowed (for
        # subclasses / mocks) but the supplied state machine is empty.
        self._queue: asyncio.Queue[Any] | None = None
        self._ws_url: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    @classmethod
    def in_process(cls, queue: asyncio.Queue[Any]) -> DashboardClient:
        """Construct a client that pulls messages from an asyncio Queue.

        The queue's items may be either already-constructed contract
        messages (BearingReport / FixEvent / NodeStatus) or wire
        envelopes (``{"type": ..., "payload": ...}`` dicts). Either is
        accepted -- the dashboard and the in-process producer can pick
        whichever fits.
        """
        instance = cls()
        instance._queue = queue
        return instance

    @classmethod
    def websocket(cls, url: str) -> DashboardClient:
        """Construct a client that subscribes via aiohttp WebSocket.

        Connection is lazy -- the WebSocket handshake happens on the
        first ``stream()`` consumer iteration so the factory itself
        does not need to be awaited.
        """
        instance = cls()
        instance._ws_url = url
        return instance

    async def stream(self) -> AsyncIterator[DashboardMessage]:
        """Yield contract messages until the stream closes.

        Routes by which factory was used. The in-process branch yields
        whatever is on the queue (deserialising envelopes when needed);
        the WebSocket branch establishes the connection on first call
        and yields decoded messages until the server closes.
        """
        if self._queue is not None:
            async for msg in self._stream_in_process():
                yield msg
            return
        if self._ws_url is not None:
            async for msg in self._stream_websocket():
                yield msg
            return
        error_msg = (
            "DashboardClient was constructed without a transport. Use "
            "DashboardClient.in_process(queue) or "
            "DashboardClient.websocket(url)."
        )
        raise RuntimeError(error_msg)

    async def _stream_in_process(self) -> AsyncIterator[DashboardMessage]:
        assert self._queue is not None  # narrowed for type-checker
        while True:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                return
            if item is None:
                # Sentinel: producer signals end-of-stream.
                return
            if isinstance(item, (BearingReport, FixEvent, NodeStatus)):
                yield item
                continue
            if isinstance(item, dict):
                decoded = _decode_envelope(item)
                if decoded is not None:
                    yield decoded
                continue
            # Unknown item type -- skip silently to keep the loop
            # robust against producer-side mistakes.

    async def _stream_websocket(self) -> AsyncIterator[DashboardMessage]:
        assert self._ws_url is not None  # narrowed for type-checker
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self._ws_url)
            async for ws_msg in self._ws:
                if ws_msg.type == aiohttp.WSMsgType.TEXT:
                    envelope = json.loads(ws_msg.data)
                elif ws_msg.type == aiohttp.WSMsgType.BINARY:
                    envelope = json.loads(ws_msg.data.decode("utf-8"))
                elif ws_msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
                else:
                    continue
                if not isinstance(envelope, dict):
                    continue
                decoded = _decode_envelope(envelope)
                if decoded is not None:
                    yield decoded
        finally:
            await self.close()

    async def close(self) -> None:
        """Release WebSocket and HTTP-session resources cleanly.

        Idempotent. Safe to call from a finally block or directly.
        """
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
