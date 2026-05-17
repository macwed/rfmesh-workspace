"""``DashboardPubSub`` -- the fusion-side publish channel for the ops dashboard.

The dashboard (``rfmesh-ops``, parallel build) is a *pure consumer* of
contract messages over a transport. The transport is here: an in-process
asyncio queue when fusion and dashboard share a host, an aiohttp
WebSocket when they do not. ``FusionService`` owns the subscription list
because the fusion server is the natural fan-out point -- bearings
arrive there, fixes are computed there, the same machine ships both to
the dashboard.

Subscriber discipline (R6 in the architect's risk list):

* Each ``publish_*`` awaits ``asyncio.gather`` across all subscribers
  with a per-subscriber timeout.
* A subscriber whose ``send`` raises (slow or disconnected) is
  logged-and-dropped from the list. Other subscribers proceed
  normally. A slow subscriber never blocks the fusion loop.
* Subscribers added during an in-flight publish are picked up on the
  next publish, never mid-fan-out (the iteration takes a snapshot).

The two subscriber kinds:

* ``InProcessSubscriber`` -- wraps an ``asyncio.Queue``. Used when the
  dashboard is hosted in the same process (the BoTH3 demo
  deployment).
* ``WebSocketSubscriber`` -- wraps an ``aiohttp.web.WebSocketResponse``.
  Used when the dashboard is a separate process (the
  development workflow). The WebSocket carries the same msgpack
  envelope as the bearers (see ``envelope.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Protocol

from rfmesh_contracts import BearingReport, FixEvent, NodeStatus

from .bearer.envelope import encode_envelope

if TYPE_CHECKING:
    from aiohttp.web import WebSocketResponse

_LOG = logging.getLogger(__name__)

_PUBLISH_PER_SUB_TIMEOUT_S: float = 1.0


class DashboardSubscriber(Protocol):
    """Structural Protocol for any dashboard subscriber.

    Implementers: ``InProcessSubscriber`` (wraps an asyncio.Queue) and
    ``WebSocketSubscriber`` (wraps an aiohttp WebSocketResponse). The
    pub-sub channel does not care which.
    """

    async def send(self, message: BearingReport | FixEvent | NodeStatus) -> None:
        """Deliver one message to the subscriber. May raise on failure."""
        ...


class InProcessSubscriber:
    """Subscriber backed by an ``asyncio.Queue``.

    The dashboard's reader-side coroutine pops messages from the queue
    and updates the figure. ``maxsize=0`` means unbounded -- with the
    matplotlib backend at 10 Hz this is fine. Setting a bound and
    dropping on full would be a silent fallback (Invariant B3) and we
    do not do that.
    """

    def __init__(self, queue: asyncio.Queue[BearingReport | FixEvent | NodeStatus]) -> None:
        self._queue = queue

    async def send(self, message: BearingReport | FixEvent | NodeStatus) -> None:
        """Push onto the queue. Blocks if a max-size queue is full."""
        await self._queue.put(message)


class WebSocketSubscriber:
    """Subscriber backed by an aiohttp ``WebSocketResponse``."""

    def __init__(self, ws: WebSocketResponse) -> None:
        self._ws = ws

    async def send(self, message: BearingReport | FixEvent | NodeStatus) -> None:
        """Send the msgpack envelope as a binary WebSocket message."""
        envelope = encode_envelope(message)
        await self._ws.send_bytes(envelope)


class DashboardPubSub:
    """In-process + WebSocket pub-sub channel for the dashboard.

    Owned by ``FusionService``; subscribers register via
    ``add_subscriber``. The fan-out is concurrent (``asyncio.gather``)
    with a per-subscriber timeout; slow/erroring subscribers are
    dropped with a logged warning, never block the fusion loop.

    ``publish_bearing`` exists for the live-bearings dashboard
    panels (BearingsPanel, PseudospectrumPanel). ``publish_fix``
    feeds the FixPanel + ellipse animation. ``publish_node_status``
    feeds NodeStatusPanel.
    """

    def __init__(self, *, per_sub_timeout_s: float = _PUBLISH_PER_SUB_TIMEOUT_S) -> None:
        if per_sub_timeout_s <= 0.0:
            msg = f"DashboardPubSub: per_sub_timeout_s must be > 0 (got {per_sub_timeout_s})."
            raise ValueError(msg)
        self._subs: list[DashboardSubscriber] = []
        self._per_sub_timeout_s = per_sub_timeout_s
        self._lock = asyncio.Lock()

    def add_subscriber(self, sub: DashboardSubscriber) -> None:
        """Register a subscriber.

        Picked up on the *next* ``publish_*`` call (iteration
        snapshots the subscriber list). Adding during fan-out does
        not race -- the in-flight publish iterates a local copy.
        """
        self._subs.append(sub)

    def remove_subscriber(self, sub: DashboardSubscriber) -> None:
        """Remove a subscriber (if present)."""
        with contextlib.suppress(ValueError):
            self._subs.remove(sub)

    @property
    def subscriber_count(self) -> int:
        """Number of currently registered subscribers (test introspection)."""
        return len(self._subs)

    async def publish_fix(self, fix: FixEvent) -> None:
        """Broadcast a ``FixEvent`` to every subscriber."""
        await self._fanout(fix)

    async def publish_bearing(self, report: BearingReport) -> None:
        """Broadcast a ``BearingReport`` to every subscriber."""
        await self._fanout(report)

    async def publish_node_status(self, status: NodeStatus) -> None:
        """Broadcast a ``NodeStatus`` to every subscriber."""
        await self._fanout(status)

    async def _fanout(self, message: BearingReport | FixEvent | NodeStatus) -> None:
        # Snapshot under lock so add_subscriber / remove_subscriber
        # mid-fanout cannot mutate the iteration target.
        async with self._lock:
            current_subs = tuple(self._subs)

        if not current_subs:
            return

        async def _send_one(sub: DashboardSubscriber) -> DashboardSubscriber | None:
            """Return the subscriber to drop, or None on success."""
            try:
                await asyncio.wait_for(
                    sub.send(message),
                    timeout=self._per_sub_timeout_s,
                )
            except Exception as exc:
                _LOG.warning(
                    "DashboardPubSub: dropping subscriber %s: %s",
                    type(sub).__name__,
                    exc,
                )
                return sub
            return None

        results = await asyncio.gather(
            *(_send_one(sub) for sub in current_subs),
            return_exceptions=False,
        )
        # Drop any subscriber whose send raised. We do this after the
        # gather so a slow/erroring sub does not affect its siblings'
        # delivery.
        to_drop = [sub for sub in results if sub is not None]
        if to_drop:
            async with self._lock:
                for sub in to_drop:
                    with contextlib.suppress(ValueError):
                        self._subs.remove(sub)


__all__ = [
    "DashboardPubSub",
    "DashboardSubscriber",
    "InProcessSubscriber",
    "WebSocketSubscriber",
]
