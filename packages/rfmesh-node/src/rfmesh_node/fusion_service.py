"""``FusionService`` -- the aggregator-side asyncio service.

The fusion server: listens for ``BearingReport`` s on its inbound
queue, batches them per ``FusionConfig.batch_window_ms``, calls
``Fuser.fuse``, and publishes the resulting ``FixEvent`` to:

* the CoT publisher (one), and
* the dashboard pub-sub channel (zero or more subscribers).

Subscriber-registration race regression (INHERITED_CONTEXT.md §5.2)
-------------------------------------------------------------------
The old repo's aggregator had a race: a subscriber registered after a
burst of measurements had arrived but before the dispatch loop iterated
would miss those measurements. The fix in this rewrite: the inbound
queue is the *single* asyncio.Queue; subscriber registration happens
on a separate channel (``DashboardPubSub.add_subscriber``); subscribers
do not consume from the inbound queue. The race is therefore prevented
by structure rather than by locking.

The regression test in ``tests/test_fusion_service.py`` -- explicit,
named ``test_subscriber_registration_race`` -- proves a subscriber
added immediately before a burst arrives gets every message in the
burst. See ``INHERITED_CONTEXT.md`` §5.2.

CoT and dashboard are dependency-injected
-----------------------------------------
``FusionService`` accepts ``cot_publisher: CotPublisher | None`` and
``dashboard_pubsub: DashboardPubSub | None`` -- both Protocol-typed
(CoT) or class-typed (pubsub). The concrete ``PyTAKCotPublisher``
from ``rfmesh-cot`` is injected by the CLI wiring layer (the
parallel build pattern -- see ADR-011 + the top-of-brief
coordination note).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from rfmesh_contracts import BearingReport, FusionConfig, NodeStatus

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rfmesh_contracts import CotPublisher, FixEvent, Fuser

    from .dashboard_pubsub import DashboardPubSub

_LOG = logging.getLogger(__name__)

# How often the staleness sweep runs. Independent of batch_window_ms
# (which paces the fuse loop) -- staleness is a slower, housekeeping
# cadence.
_STALENESS_SWEEP_INTERVAL_S: float = 1.0


class FusionService:
    """Aggregator asyncio service: batch -> fuse -> publish."""

    def __init__(
        self,
        config: FusionConfig,
        fuser: Fuser,
        cot_publisher: CotPublisher | None = None,
        dashboard_pubsub: DashboardPubSub | None = None,
    ) -> None:
        """Wire up the fusion service.

        Args:
            config: ``FusionConfig`` -- the window, staleness, and
                min-bearings thresholds.
            fuser: A ``Fuser`` -- typically
                ``rfmesh_fusion.StansfieldMLEFuser``, but any
                Protocol conformer is fine (tests inject a fake).
            cot_publisher: A ``CotPublisher`` -- typically
                ``rfmesh_cot.PyTAKCotPublisher``. ``None`` disables
                CoT output (headless bench mode).
            dashboard_pubsub: The dashboard fan-out channel.
                ``None`` disables dashboard output.
        """
        self._config = config
        self._fuser = fuser
        self._cot_publisher = cot_publisher
        self._dashboard_pubsub = dashboard_pubsub

        # Inbound queue for bearings + statuses, fed by the
        # transport layer (Bearer.receive() drained on a bearer
        # task that pushes here). Unbounded -- back-pressure is
        # in the bearer layer's UDP socket buffer, not at the
        # asyncio level (where backpressure would drop bearings
        # and that would be a silent-fallback).
        self._inbox: asyncio.Queue[BearingReport | NodeStatus] = asyncio.Queue()

        # Stale-node bookkeeping: node_id -> last_seen_t_unix_s
        # (wall-clock receipt time, not message timestamp -- a node
        # whose clock drifted should not be misclassified as stale).
        self._last_seen_s: dict[str, float] = {}

        self._tasks: list[asyncio.Task[None]] = []
        self._stopping: asyncio.Event = asyncio.Event()

    @property
    def inbox(self) -> asyncio.Queue[BearingReport | NodeStatus]:
        """The inbound queue -- bearer tasks push here.

        Exposed for the bearer-glue task that the node-runtime
        layer creates (and for tests that simulate bearer traffic
        directly).
        """
        return self._inbox

    async def run(self) -> None:
        """Run the fuse loop + staleness sweep until ``shutdown``."""
        self._stopping.clear()
        self._tasks = [
            asyncio.create_task(self._fuse_loop(), name="fusion-fuse-loop"),
            asyncio.create_task(self._staleness_loop(), name="fusion-staleness"),
        ]
        try:
            await self._stopping.wait()
        finally:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = []

    async def shutdown(self) -> None:
        """Signal the loops to stop. Safe to call multiple times."""
        self._stopping.set()

    async def _fuse_loop(self) -> None:
        """Drain the inbox in ``batch_window_ms`` slices; fuse + publish."""
        window_s = self._config.batch_window_ms / 1000.0
        while not self._stopping.is_set():
            batch = await self._collect_window(window_s)
            self._update_last_seen(batch)
            bearings = self._filter_live_bearings(batch)
            if len(bearings) < self._config.min_bearings_for_fix:
                continue
            try:
                fix = self._fuser.fuse(bearings, self._config)
            except Exception as exc:
                _LOG.exception("FusionService: fuser raised: %s", exc)
                continue
            if fix is None:
                # The Protocol's "cannot responsibly solve" signal.
                # Not an error; the operator sees the live bearings
                # on the dashboard and waits for geometry to
                # improve.
                continue
            await self._publish_fix(fix)

    async def _collect_window(self, window_s: float) -> list[BearingReport | NodeStatus]:
        """Drain the inbox for up to ``window_s`` seconds.

        Returns the list of messages received within the window. An
        empty window returns an empty list -- the fuse loop's
        downstream filter then skips publishing, naturally throttling
        when no bearings are arriving.

        Crucially this collects *all* messages currently in the
        inbox, then waits for the rest of the window (or until
        ``shutdown``) for late arrivals. This matches the salvaged
        old-repo behaviour where the aggregator's time-window is
        the unit of batching, not the rate-limit.
        """
        deadline = asyncio.get_running_loop().time() + window_s
        batch: list[BearingReport | NodeStatus] = []
        # Drain whatever is already queued, non-blocking.
        while not self._inbox.empty():
            batch.append(self._inbox.get_nowait())
        # Wait for the rest of the window for late arrivals.
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0.0:
                break
            if self._stopping.is_set():
                break
            try:
                message = await asyncio.wait_for(self._inbox.get(), timeout=remaining)
            except TimeoutError:
                break
            batch.append(message)
        return batch

    def _update_last_seen(self, batch: Iterable[BearingReport | NodeStatus]) -> None:
        now_s = asyncio.get_running_loop().time()
        for message in batch:
            self._last_seen_s[message.node_id] = now_s

    def _filter_live_bearings(
        self, batch: Iterable[BearingReport | NodeStatus]
    ) -> list[BearingReport]:
        """Keep only BearingReports from nodes that are not stale.

        A node is stale if its most recent message arrived more
        than ``node_stale_after_s`` ago. Status messages are
        consumed by the staleness map but never passed to the
        fuser (status is a heartbeat, not a bearing).
        """
        now_s = asyncio.get_running_loop().time()
        stale_cutoff_s = now_s - self._config.node_stale_after_s
        out: list[BearingReport] = []
        for message in batch:
            if not isinstance(message, BearingReport):
                continue
            last = self._last_seen_s.get(message.node_id)
            if last is None or last < stale_cutoff_s:
                # Node has not been seen recently enough; skip.
                continue
            out.append(message)
        return out

    async def _publish_fix(self, fix: FixEvent) -> None:
        """Send to CoT (sync) and dashboard (async fan-out)."""
        if self._cot_publisher is not None:
            try:
                self._cot_publisher.publish(fix)
            except Exception as exc:
                # CoT failure does not kill the dashboard channel.
                # Log loudly and continue -- the operator sees the
                # fix on the dashboard, the CoT outage is itself
                # surfaced via the next NodeStatus from the
                # fusion-server side (future ticket; v1.0 just logs).
                _LOG.exception("FusionService: CoT publish failed: %s", exc)
        if self._dashboard_pubsub is not None:
            await self._dashboard_pubsub.publish_fix(fix)

    async def _staleness_loop(self) -> None:
        """Periodically prune the last-seen map. Light housekeeping."""
        while not self._stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=_STALENESS_SWEEP_INTERVAL_S,
                )
            # We do not actively drop entries here in v1.0 -- the
            # _filter_live_bearings gate already excludes stale
            # nodes; the map can grow at most to "ever seen" which
            # is small. A future ticket may evict entries older
            # than a long timeout. The sweep loop exists so a
            # future eviction lands without restructuring.

    # ------------------------------------------------------------------
    # Test convenience: bypass the bearer for direct injection.
    # ------------------------------------------------------------------

    def push_for_test(self, message: BearingReport | NodeStatus) -> None:
        """Push a message directly into the inbox.

        Synchronous wrapper around ``inbox.put_nowait``. Used by
        the test suite (notably the subscriber-registration race
        regression) to simulate bearer traffic without spinning up
        UDP sockets.
        """
        self._inbox.put_nowait(message)


# Optional re-export so callers can ``from rfmesh_node.fusion_service
# import publish_callback_type`` for typing -- left for future
# expansion; not used in v1.0.
PublishCallback = "Callable[[FixEvent], Awaitable[None]]"


__all__ = [
    "FusionService",
    "PublishCallback",
]
