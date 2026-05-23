"""``Node`` -- the asyncio service that runs one mesh node.

Composition root for one node: owns one ``Receiver`` (or
``CoherentReceiver`` for L2 nodes), one or more ``BearingEstimator`` s,
one ``Bearer``, and the heartbeat task. Does NOT own the
``Fuser`` or the ``CotPublisher`` -- those live in ``FusionService``
which runs on the designated fusion-server node.

Task graph (binding shape per docs/design/ops-architecture.md §2.2)
-------------------------------------------------------------------

* Receiver.read(N) reads IQ blocks. For each block:
    - L1 estimator (if L1 active) observes; on sweep-end emits.
    - L2 MUSIC / Capon estimators (if active) estimate.
* Each emitted ``BearingReport`` goes through the bearer.
* Heartbeat task emits ``NodeStatus`` at
  ``BearerConfig.heartbeat_interval_s``.

This v1.0 ``Node`` is the smallest honest composition root: it owns
the lifecycle, fires the heartbeat, and routes a Bearer protocol
endpoint. The estimator-driving loop (the *contents* of "for each
block") is a wiring concern that depends on the active set; for
v1.0 we expose ``Node`` as a *runtime container* that brings up
the receiver + bearer + heartbeat and accepts injected estimators,
so the test suite can verify the lifecycle without needing every
DSP estimator to be plumbed in to the receiver's read loop. The
estimator-loop wiring is a later WS-CD ticket layered on top.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from rfmesh_contracts import (
    Bearer,
    BearingReport,
    Capability,
    NodeConfig,
    NodeStatus,
    Receiver,
)

from .capabilities import build_estimators, detect_active_capabilities

if TYPE_CHECKING:
    from rfmesh_contracts import BearingEstimator

    from .l1_sweep import L1SweepLoop


_LOG = logging.getLogger(__name__)


class Node:
    """Asyncio service for one mesh node.

    Lifecycle:

    * Constructor: bind config + receiver; do not open the device.
    * ``await run()``: open the receiver, detect capabilities, build
      estimators, start the heartbeat task, await ``shutdown``.
    * ``await shutdown()``: signal stop; ``run`` cleans up.
    """

    def __init__(
        self,
        config: NodeConfig,
        *,
        receiver: Receiver,
        bearer: Bearer | None = None,
        sweep_loop: L1SweepLoop | None = None,
    ) -> None:
        """Bind the node config and the receiver (and optionally the bearer).

        Capability mismatch is *not* checked here -- it requires the
        receiver to be open (capabilities() is the live snapshot).
        Construction may therefore fail later, at ``run``, with
        ``CapabilityMismatchError``. We could open the receiver
        eagerly in __init__, but doing so makes Node construction
        a side-effectful operation -- the architect's preference is
        that import / construction is side-effect-free, side
        effects happen in ``run``.
        """
        self._config = config
        self._receiver = receiver
        self._bearer = bearer
        self._sweep_loop = sweep_loop
        self._estimators: tuple[BearingEstimator, ...] = ()
        self._active_capabilities: tuple[Capability, ...] = ()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()
        self._running = False

    async def run(self) -> None:
        """Bring the node up; run until ``shutdown`` is called."""
        if self._running:
            msg = "Node.run called twice; create a new Node instance."
            raise RuntimeError(msg)
        self._running = True
        self._stopping.clear()
        try:
            self._open_receiver()
            self._build_pipeline()
            self._tasks = [
                asyncio.create_task(self._heartbeat_loop(), name="node-heartbeat"),
            ]
            # L1 sweep loop: only when an L1 sweep was wired (servo present
            # and L1_RSSI active). It drives the receiver -> servo ->
            # estimator -> bearer cycle the v1.0 container otherwise omits.
            if self._sweep_loop is not None:
                self._tasks.append(
                    asyncio.create_task(
                        self._sweep_loop.run(self._stopping), name="node-l1-sweep"
                    )
                )
            await self._stopping.wait()
        finally:
            await self._teardown()

    async def shutdown(self) -> None:
        """Signal the run loop to stop. Idempotent."""
        self._stopping.set()

    @property
    def active_capabilities(self) -> tuple[Capability, ...]:
        """The active capability set after ``run`` has wired the pipeline."""
        return self._active_capabilities

    @property
    def estimators(self) -> tuple[BearingEstimator, ...]:
        """The built estimator instances (test introspection)."""
        return self._estimators

    # ------------------------------------------------------------------
    # Internal: lifecycle steps.
    # ------------------------------------------------------------------

    def _open_receiver(self) -> None:
        """Open + configure the receiver; surface failures loudly."""
        self._receiver.open()
        self._receiver.configure(self._config)

    def _build_pipeline(self) -> None:
        """Detect capabilities + build estimators. Raises on mismatch."""
        self._active_capabilities = detect_active_capabilities(
            self._config.capabilities,
            self._receiver,
            self._config.array,
        )
        self._estimators = build_estimators(
            self._active_capabilities,
            self._config.sdr,
            self._config.array,
            self._config.heading_deg,
            node_id=self._config.node_id,
            node_position=self._config.position,
            receiver=self._receiver,
        )

    async def _heartbeat_loop(self) -> None:
        """Emit ``NodeStatus`` every ``heartbeat_interval_s``."""
        interval_s = self._config.bearer.heartbeat_interval_s
        while not self._stopping.is_set():
            try:
                status = self._build_status()
                if self._bearer is not None:
                    self._bearer.send_status(status)
            except Exception:
                _LOG.exception("Node.heartbeat: send failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=interval_s)

    def _build_status(self) -> NodeStatus:
        # Surface bearer health into status_detail when the bearer exposes
        # a health_summary() method (e.g. BothBearer reports "LoRa bearer
        # down, Wi-Fi only" once the LoRa half raises NotImplementedError).
        # Wi-Fi-only and LoRa-only bearers omit the method; default to "".
        # See INTERFACES.md §3 NodeStatus.status_detail for the canonical
        # example strings.
        status_detail = ""
        summary_fn = getattr(self._bearer, "health_summary", None)
        if callable(summary_fn):
            try:
                status_detail = str(summary_fn() or "")
            except Exception:
                _LOG.exception("Node._build_status: bearer.health_summary failed")
                status_detail = ""
        return NodeStatus(
            node_id=self._config.node_id,
            t_unix_ns=time.time_ns(),
            position=self._config.position,
            active_capabilities=tuple(self._active_capabilities),
            gnss_locked=False,
            healthy=True,
            status_detail=status_detail,
        )

    async def _teardown(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        try:
            self._receiver.close()
        except Exception as exc:
            _LOG.exception("Node.teardown: receiver.close failed: %s", exc)
        if self._bearer is not None:
            try:
                self._bearer.close()
            except Exception as exc:
                _LOG.exception("Node.teardown: bearer.close failed: %s", exc)
        self._running = False

    # ------------------------------------------------------------------
    # Test convenience.
    # ------------------------------------------------------------------

    async def emit_for_test(self, report: BearingReport) -> None:
        """Send a BearingReport via the bearer (no estimator loop).

        v1.0 helper: lets tests verify the bearer wiring without
        needing the full receiver-driven estimator loop. Production
        code will wire ``Receiver.read`` -> ``BearingEstimator.estimate``
        -> ``Bearer.send_bearing`` in a follow-up ticket; this
        helper makes the lifecycle / bearer contract testable now.
        """
        if self._bearer is None:
            msg = "Node.emit_for_test: no bearer attached."
            raise RuntimeError(msg)
        self._bearer.send_bearing(report)


__all__ = ["Node"]
