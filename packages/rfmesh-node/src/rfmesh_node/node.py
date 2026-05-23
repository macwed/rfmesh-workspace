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
from .command_channel import CommandChannel
from .commands import AllStopCommand, ManualSteerCommand

if TYPE_CHECKING:
    from rfmesh_contracts import BearingEstimator
    from rfmesh_servo.driver import ServoDriver

    from .l1_sweep import L1SweepLoop
    from .rendezvous import RendezvousLoop
    from .runtime_config import CommandEndpointConfig


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
        rendezvous_loop: RendezvousLoop | None = None,
        servo: ServoDriver | None = None,
        command_endpoint: CommandEndpointConfig | None = None,
    ) -> None:
        """Bind the node config and the receiver (and optionally the bearer).

        ``command_endpoint`` (ADR-022): when set + enabled, ``Node.run`` opens
        a long-lived WebSocket to the backend, sends a ``node_hello`` frame
        (capability handshake the web UI needs to clamp the manual-steer
        slider, ADR-021 §"Manual-steer safety"), and dispatches inbound
        commands. The dispatched-handler is a refusal stub here -- a real
        servo-action handler ships with ``NodeController`` in a follow-up
        ADR (the council-flagged race between mode-switch preemption and
        in-flight ``RendezvousLoop._refine_once`` writes is unresolved).
        B3: the stub never silently accepts; it refuses loudly.

        Capability mismatch is *not* checked here -- it requires the
        receiver to be open. Construction may therefore fail later, at
        ``run``, with ``CapabilityMismatchError``.
        """
        self._config = config
        self._receiver = receiver
        self._bearer = bearer
        self._sweep_loop = sweep_loop
        self._rendezvous_loop = rendezvous_loop
        # Servo lifecycle is owned here (not in the sweep loop) so the sweep
        # and rendezvous loops can share one connected servo without
        # re-enumerating the USB-CDC link on every mode flip (ADR-019).
        self._servo = servo
        # Operator-facing rendezvous state, surfaced via NodeStatus.status_detail.
        self._rendezvous_status_detail = ""
        self._estimators: tuple[BearingEstimator, ...] = ()
        self._active_capabilities: tuple[Capability, ...] = ()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()
        self._running = False
        # ADR-022 comms-mode command channel.
        self._command_endpoint = command_endpoint
        self._command_channel: CommandChannel | None = None

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
            # Connect the servo once, here, before any servo-driving task
            # starts (ADR-019: lifecycle hoisted out of L1SweepLoop so the
            # sweep and rendezvous loops share one connected servo).
            if self._servo is not None:
                await asyncio.to_thread(self._servo.connect)
            self._tasks = [
                asyncio.create_task(self._heartbeat_loop(), name="node-heartbeat"),
            ]
            # Servo-driving task: exactly one, so only one writer touches the
            # single-outstanding-command servo at a time (ADR-019).
            #  * rendezvous wired -> the supervisor owns the servo and
            #    time-shares it with the jammer sweep (link-hold <-> DF sweep);
            #  * else L1 sweep wired -> the plain blind-sweep loop;
            #  * else heartbeat-only (the v1.0 container behaviour).
            if self._rendezvous_loop is not None:
                self._tasks.append(
                    asyncio.create_task(self._rendezvous_supervisor(), name="node-rendezvous")
                )
            elif self._sweep_loop is not None:
                self._tasks.append(
                    asyncio.create_task(self._sweep_loop.run(self._stopping), name="node-l1-sweep")
                )
            # ADR-022 comms-mode: phone the backend so link.html can see
            # this node + clamp the manual-steer slider to the calibrated
            # arc. Optional -- enabled iff command_endpoint was provided
            # and command_endpoint.enabled is true (the latter checked at
            # construction by the CLI builder).
            if self._command_endpoint is not None and self._command_endpoint.backend_ws_url:
                self._command_channel = CommandChannel(
                    backend_ws_url=self._command_endpoint.backend_ws_url,
                    node_id=self._config.node_id,
                    handler=self._handle_command,
                    hello_payload_fn=self._build_hello_payload,
                )
                self._tasks.append(
                    asyncio.create_task(
                        self._command_channel.run(self._stopping),
                        name="node-command-channel",
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

    async def _rendezvous_supervisor(self) -> None:
        """Time-share the servo: acquire+hold the directional link, break to DF.

        ADR-019. Owns the servo for the whole cycle so it is the only ``move``
        writer. Acquire the peer link; on lock, hold for ``link_hold_s`` then
        yield the servo to one jammer ``L1SweepLoop`` pass and re-acquire. On
        failure, surface the loud reason and (if a sweep is wired) keep doing
        jammer DF while retrying the link.
        """
        rv = self._rendezvous_loop
        if rv is None:  # pragma: no cover - guarded by the caller
            return
        cfg = rv.config
        sweep = self._sweep_loop
        while not self._stopping.is_set():
            locked = await rv.acquire(self._stopping)
            self._rendezvous_status_detail = rv.last_status
            if not locked:
                # FAILED: do useful jammer DF (if wired) instead of spinning,
                # then pause and retry the link.
                if sweep is not None and not self._stopping.is_set():
                    await sweep.run_once(self._stopping)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=cfg.retry_pause_s)
                continue
            # LOCKED: hold the link, then break for one jammer sweep, re-acquire.
            await rv.hold(self._stopping, cfg.link_hold_s)
            if sweep is not None and not self._stopping.is_set():
                await sweep.run_once(self._stopping)

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
        # Rendezvous state is the active operational status when running; it
        # takes precedence over bearer health so the dashboard shows lock /
        # loud-failure reasons (ADR-019, E1 status_detail channel).
        if self._rendezvous_status_detail:
            status_detail = self._rendezvous_status_detail
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
        if self._servo is not None:
            try:
                self._servo.close()
            except Exception as exc:
                _LOG.exception("Node.teardown: servo.close failed: %s", exc)
        self._running = False

    # ------------------------------------------------------------------
    # Test convenience.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Comms-mode (ADR-022): node_hello + command handler stub.
    # ------------------------------------------------------------------

    def _build_hello_payload(self) -> dict[str, object]:
        """Build the one-shot ``node_hello`` capability snapshot.

        Sent by ``CommandChannel`` on every (re)connect. The backend
        caches it on ``NodeWsRegistry`` and serves it from
        ``GET /node/{node_id}/capabilities``; ``link.html`` reads that
        to clamp the manual-steer slider to the calibrated arc
        (ADR-021 §"Manual-steer safety" layer 3).

        Calibrated-arc honesty (B3): v1 reports the *configured* arc
        from the sweep / rendezvous config and tags ``cal_provenance``
        as ``"config"``. When the firmware's ``get_calibration``
        roundtrip is wired in here (per memory CAL_PERSIST cosmetic
        quirk), the source flips to ``"firmware-nvs"`` and the arc
        comes from there.
        """
        # The configured arc is the L1 sweep's calibrated range; if a
        # rendezvous block is present, its arc is contained in the sweep
        # arc (validated by NodeRuntimeConfig._coherence).
        sweep_loop = self._sweep_loop
        rv_loop = self._rendezvous_loop
        if sweep_loop is not None:
            cfg = sweep_loop._cfg
            arc_min, arc_max = cfg.min_deg, cfg.max_deg
        elif rv_loop is not None:
            arc_min = rv_loop.config.min_servo_deg
            arc_max = rv_loop.config.max_servo_deg
        else:
            # No servo-driving loop -> there is no arc to clamp the UI to.
            arc_min = None
            arc_max = None

        peer: dict[str, object] | None = None
        if rv_loop is not None:
            pp = rv_loop.config.peer_position
            peer = {
                "node_id": rv_loop.config.peer_node_id,
                "lat_deg": pp.lat_deg,
                "lon_deg": pp.lon_deg,
            }

        return {
            "node_id": self._config.node_id,
            "schema_version": self._config.schema_version,
            "active_capabilities": [c.value for c in self._active_capabilities],
            "heading_deg": self._config.heading_deg,
            "position": {
                "lat_deg": self._config.position.lat_deg,
                "lon_deg": self._config.position.lon_deg,
            },
            "calibrated_geographic_arc_deg": (
                None
                if arc_min is None or arc_max is None
                else {"min": float(arc_min), "max": float(arc_max)}
            ),
            "cal_provenance": "config",
            "peer": peer,
            "controller_ready": False,
        }

    async def _handle_command(self, command: ManualSteerCommand | AllStopCommand) -> None:
        """Stub command handler (ADR-022): refuse loudly, do not touch the servo.

        The real handler ships with ``NodeController`` in a follow-up ADR;
        the council code-reviewer's BLOCK on item 2 (mode-switch preemption
        racing in-flight ``RendezvousLoop._refine_once`` writes) is unresolved.
        Until then this handler keeps B3 by never silently accepting a
        steer the system cannot honour, and surfaces the refusal back
        through the WS so ``link.html`` can render a red toast.
        """
        kind = command.kind
        reason = (
            "NodeController not yet wired (ADR-022 ships transport + capability "
            "handshake; servo-action handler waits on NodeController ADR)."
        )
        _LOG.warning(
            "Node %s: refusing %s command from %s -- %s",
            self._config.node_id,
            kind,
            command.requestor_id,
            reason,
        )
        if self._command_channel is not None:
            await self._command_channel.send_response(
                {
                    "kind": "command_refused",
                    "node_id": self._config.node_id,
                    "refused_kind": kind,
                    "requestor_id": command.requestor_id,
                    "reason": reason,
                }
            )

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
