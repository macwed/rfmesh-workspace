"""Node-side WebSocket client for the comms-mode command channel.

ADR-018 (PROPOSED 2026-05-23) wires deployed nodes to the both3 backend
through a long-lived WebSocket the node initiates. The backend pushes
manual-steer + all-stop commands down that socket; the node consumes
them and forwards to a handler (typically the future ``NodeController``
state machine).

Why node-initiated WS (vs backend webhook or node polling): a field
node sits behind cellular NAT in the operational environment — the
backend cannot reach it unsolicited. WS gives single-socket
bidirectional flow with a clean disconnect signal, no polling latency,
no NAT punch-through.

Why aiohttp (vs websockets / starlette client): ``rfmesh-node`` already
depends on aiohttp for ``WebSocketSubscriber`` (publish-side). Reusing
the same library keeps dependencies tight.

Reconnect policy: exponential backoff with jitter, capped. Commands
issued during a disconnect are LOST — the backend's per-node registry
returns 503 to the UI, the UI surfaces "offline" honestly. This is
correct B3 behaviour: silently queuing commands would mean the operator
sees "command accepted" while nothing happens.

Out of scope for this module:

* The actual servo-move action a manual_steer command triggers. That
  lives in NodeController (future ticket).
* Outbound traffic from node to backend (bearings + node status). The
  HTTP ``HttpBearer`` (BartekDu's commit `6ca3c43`) carries those.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .commands import (
    AllStopCommand,
    ClearFaultCommand,
    ManualSteerCommand,
    SendCommsMessageCommand,
    parse_command,
)

_LOG = logging.getLogger(__name__)

CommandHandler = Callable[
    [ManualSteerCommand | AllStopCommand | ClearFaultCommand | SendCommsMessageCommand],
    Awaitable[None],
]
"""Async callable consuming one command. Implemented by NodeController
(or a test fake). ``SendCommsMessageCommand`` was added in ADR-025 Iter 4 --
the controller routes it to ``CommsLoop.queue_outbound`` when comms
mode is active; in DF mode the controller refuses (B3 -- loud no-op
rather than silent drop)."""

HelloPayloadFn = Callable[[], dict[str, Any]]
"""Returns the ``node_hello`` payload sent once per WS connect (ADR-022).

Recomputed on every (re)connect so capability changes since the last
session reach the backend."""

# Reconnect backoff: starts at _RECONNECT_BASE_S, doubles each failure
# (with ±25 % jitter to spread herd-reconnects), capped at _RECONNECT_MAX_S.
# Numbers chosen so a momentary network flap reconnects fast (~1 s) and
# a sustained outage doesn't hammer the backend (~30 s ceiling).
_RECONNECT_BASE_S: float = 1.0
_RECONNECT_MAX_S: float = 30.0
_RECONNECT_JITTER: float = 0.25


class CommandChannel:
    """Long-lived node→backend WS client.

    Lifecycle:

    * ``run(stopping)`` is the asyncio task ``Node`` schedules. It loops
      forever (until ``stopping`` is set), opening a WebSocket to
      ``backend_ws_url + /ws/node/{node_id}`` and dispatching every
      incoming text frame to ``handler``.
    * On WS error / disconnect the loop sleeps ``_reconnect_delay()`` and
      retries. ``stopping.set()`` aborts the sleep + the connection.
    * Bad frames (non-JSON, schema-invalid) are logged + dropped (B3:
      loud, not silent).
    """

    def __init__(
        self,
        *,
        backend_ws_url: str,
        node_id: str,
        handler: CommandHandler,
        hello_payload_fn: HelloPayloadFn | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Bind config + dispatch handler.

        Args:
            backend_ws_url: Base WS URL of the backend, e.g.
                ``ws://10.0.0.1:8000``. The node_id path is appended.
            node_id: Stable identifier (matches ``NodeConfig.node_id``).
            handler: Async callable invoked once per parsed command.
                Exceptions raised by the handler are logged and do NOT
                tear down the channel — a flaky NodeController must not
                kill the command-channel task.
            hello_payload_fn: Optional zero-arg callable producing the
                ``node_hello`` payload sent once per connect (ADR-022 web
                UI capability handshake). ``None`` disables the hello
                frame (legacy / test path).
            session: Optional aiohttp ClientSession. When None, the
                channel owns its session (closes on shutdown).
        """
        self._url = f"{backend_ws_url.rstrip('/')}/ws/node/{node_id}"
        self._node_id = node_id
        self._handler = handler
        self._hello_payload_fn = hello_payload_fn
        self._session = session
        self._owns_session = session is None
        self._attempt = 0
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def run(self, stopping: asyncio.Event) -> None:
        """Connect + dispatch loop. Returns when ``stopping`` is set."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            while not stopping.is_set():
                try:
                    await self._connect_and_serve(stopping)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _LOG.warning("CommandChannel %s: connection error: %s", self._node_id, exc)
                if stopping.is_set():
                    break
                delay = self._reconnect_delay()
                _LOG.info(
                    "CommandChannel %s: reconnecting in %.1f s (attempt %d)",
                    self._node_id,
                    delay,
                    self._attempt,
                )
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=delay)
                    # stopping was set during the sleep → exit cleanly.
                    break
                except TimeoutError:
                    continue  # timeout = backoff elapsed; loop and retry
        finally:
            if self._owns_session and self._session is not None:
                await self._session.close()

    async def _connect_and_serve(self, stopping: asyncio.Event) -> None:
        """Open one WS, send the hello frame, dispatch frames until disconnect."""
        assert self._session is not None
        async with self._session.ws_connect(self._url) as ws:
            _LOG.info("CommandChannel %s: connected to %s", self._node_id, self._url)
            self._attempt = 0  # reset backoff on a successful connect
            self._ws = ws
            # ADR-022 capability handshake: send node_hello once per connect.
            # If the call fails (network flap right after connect, ws closed
            # by backend), log and continue; the next reconnect retries.
            if self._hello_payload_fn is not None:
                try:
                    payload = self._hello_payload_fn()
                    payload.setdefault("kind", "node_hello")
                    await ws.send_str(json.dumps(payload, separators=(",", ":")))
                except Exception:
                    _LOG.exception("CommandChannel %s: node_hello send failed", self._node_id)
            try:
                while not stopping.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=0.5)
                    except TimeoutError:
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._dispatch(msg.data)
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
            finally:
                self._ws = None

    async def send_response(self, payload: dict[str, Any]) -> bool:
        """Send a node->backend JSON frame on the open WS.

        Used by the command handler to surface refusals back to the UI
        (B3: never silently drop). Returns True on send, False if the WS
        is not currently connected. Does not raise on send failure.
        """
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send_str(json.dumps(payload, separators=(",", ":")))
        except Exception:
            _LOG.exception("CommandChannel %s: send_response failed", self._node_id)
            return False
        return True

    async def _dispatch(self, frame: str) -> None:
        """Parse one frame; invoke handler. Bad frames logged + dropped."""
        try:
            command = parse_command(frame)
        except ValueError as exc:
            _LOG.warning("CommandChannel %s: dropping bad frame: %s", self._node_id, exc)
            return
        try:
            await self._handler(command)
        except Exception:
            _LOG.exception(
                "CommandChannel %s: handler raised on %s; channel stays open",
                self._node_id,
                command.kind,
            )

    def _reconnect_delay(self) -> float:
        """Compute the next backoff interval in seconds.

        ``_RECONNECT_BASE_S * 2^attempt`` with ±25 % uniform jitter,
        capped at ``_RECONNECT_MAX_S``. Increments ``_attempt`` each
        call so successive failures back off; ``_connect_and_serve``
        resets ``_attempt`` to 0 on a successful connect.
        """
        delay = min(_RECONNECT_BASE_S * (2**self._attempt), _RECONNECT_MAX_S)
        jitter = delay * _RECONNECT_JITTER * (2.0 * random.random() - 1.0)  # noqa: S311
        self._attempt += 1
        return float(max(0.1, delay + jitter))


__all__ = ["CommandChannel", "CommandHandler", "HelloPayloadFn"]
