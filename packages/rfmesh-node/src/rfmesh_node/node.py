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
    PeerLink,
    Receiver,
)

from .capabilities import build_estimators, detect_active_capabilities
from .command_channel import CommandChannel
from .commands import (
    AllStopCommand,
    ClearFaultCommand,
    ManualSteerCommand,
    SendCommsMessageCommand,
)
from .controller import NodeController

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
        # ADR-024 NodeController -- owns servo when wired (replaces
        # the stub _handle_command of ADR-022 + folds the _rendezvous_supervisor).
        self._controller: NodeController | None = None

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
            # single-outstanding-command servo at a time (ADR-019/ADR-024).
            #  * comms-mode wired (command_endpoint + servo) -> NodeController
            #    arbitrates between SWEEPING/ACQUIRED_PEER/MANUAL_HOLD/PARKED
            #    /FAULT modes via cancel-drained handoff (ADR-024).
            #  * else rendezvous wired -> the legacy supervisor owns the servo
            #    (kept one release for backwards-compat with BartekDu's path).
            #  * else L1 sweep wired -> the plain blind-sweep loop.
            #  * else heartbeat-only (the v1.0 container behaviour).
            controller_eligible = (
                self._servo is not None
                and self._command_endpoint is not None
                and self._command_endpoint.backend_ws_url
            )
            if controller_eligible:
                # Cached calibrated arc for manual-steer validation. v1
                # uses the configured sweep / rendezvous arc as the
                # source (cal_provenance="config", per ADR-022); when
                # the firmware get_calibration round-trip is wired in,
                # this flips to "firmware-nvs". The arc is in servo-frame
                # degrees here -- the same numbers the slider clamps to.
                cal_arc = self._cached_calibrated_arc()
                self._controller = NodeController(
                    servo=self._servo,
                    sweep_loop=self._sweep_loop,
                    rendezvous_loop=self._rendezvous_loop,
                    calibrated_arc_deg=cal_arc,
                    node_id=self._config.node_id,
                    on_state_change=self._on_controller_state_change,
                )
                self._tasks.append(
                    asyncio.create_task(
                        self._controller.run(self._stopping),
                        name="node-controller",
                    )
                )
            elif self._rendezvous_loop is not None:
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
            peer_links=self._build_peer_links(),
        )

    def _build_peer_links(self) -> tuple[PeerLink, ...] | None:
        """Build the ADR-026 ``NodeStatus.peer_links`` snapshot.

        Returns ``None`` on a node with no rendezvous loop configured
        (legacy semantics preserved). Returns an empty tuple on a 1.4.0+
        node that has rendezvous configured but no live peer info yet
        -- distinct from None per ADR-026 §Change E.

        link_margin_db field is left as None in v1.4.0; live link margin
        measurement is gate-5 of the soldier-grade checklist and lands
        with ADR-025 comms-mode hardware. The peer_node_id +
        last_lock_t_unix_ns fields are populated from RendezvousLoop's
        last successful refine.
        """
        rv = self._rendezvous_loop
        if rv is None:
            return None
        last_report = rv.last_report
        last_lock_ns: int | None = last_report.t_unix_ns if last_report is not None else None
        return (
            PeerLink(
                peer_node_id=rv.config.peer_node_id,
                last_lock_t_unix_ns=last_lock_ns,
                link_margin_db=None,
            ),
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

    def _cached_calibrated_arc(self) -> tuple[float, float] | None:
        """The configured servo arc, in servo-frame degrees (B3 source).

        Source of truth until the firmware ``get_calibration`` roundtrip
        is wired in here (per memory CAL_PERSIST cosmetic quirk); then
        flips to ``"firmware-nvs"`` in the hello's ``cal_provenance``.
        This arc is what ``NodeController._validate_manual_angle``
        clamps to, and what ``link.js`` clamps the slider to.
        """
        if self._sweep_loop is not None:
            cfg = self._sweep_loop._cfg
            return (float(cfg.min_deg), float(cfg.max_deg))
        if self._rendezvous_loop is not None:
            rv = self._rendezvous_loop.config
            return (float(rv.min_servo_deg), float(rv.max_servo_deg))
        return None

    def _build_hello_payload(self) -> dict[str, object]:
        """Build the ``node_hello`` capability snapshot.

        Sent by ``CommandChannel`` on every (re)connect. The backend
        caches it on ``NodeWsRegistry`` and serves it from
        ``GET /node/{node_id}/capabilities``; ``link.html`` reads that
        to clamp the manual-steer slider to the calibrated arc
        (ADR-021 §"Manual-steer safety" layer 3).

        ADR-024 additions: ``controller_ready`` flips to ``true`` when a
        ``NodeController`` is wired, and the ``state`` /
        ``manual_hold_expires_at_ns`` / ``last_commanded_angle_deg``
        fields are populated from the controller snapshot so the UI can
        render the 5-state badge + countdown without polling.

        Calibrated-arc honesty (B3): v1 reports the *configured* arc and
        tags ``cal_provenance`` as ``"config"``.
        """
        arc = self._cached_calibrated_arc()
        rv_loop = self._rendezvous_loop
        peer: dict[str, object] | None = None
        if rv_loop is not None:
            pp = rv_loop.config.peer_position
            peer = {
                "node_id": rv_loop.config.peer_node_id,
                "lat_deg": pp.lat_deg,
                "lon_deg": pp.lon_deg,
            }

        controller_snap: dict[str, object] = {}
        if self._controller is not None:
            controller_snap = self._controller.snapshot()

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
                None if arc is None else {"min": arc[0], "max": arc[1]}
            ),
            "cal_provenance": "config",
            "peer": peer,
            "controller_ready": self._controller is not None,
            "state": controller_snap.get("state"),
            "status_detail": controller_snap.get("status_detail", ""),
            "manual_hold_expires_at_ns": controller_snap.get("manual_hold_expires_at_ns"),
            "last_commanded_angle_deg": controller_snap.get("last_commanded_angle_deg"),
        }

    async def _on_controller_state_change(self, payload: dict[str, object]) -> bool:
        """ADR-024 broadcast hook: push state-change frame back over the WS.

        Routes through the same ``CommandChannel.send_response`` used by
        the refusal-stub of ADR-022; backend's ``/ws/node/{id}`` handler
        forwards ``kind=node_state`` frames to UI subscribers so the badge
        flips within ~200 ms of the firmware event (per demo-integrity
        rec — event-driven link_state, not heartbeat cadence).
        """
        if self._command_channel is None:
            return False
        return await self._command_channel.send_response(payload)

    async def _handle_command(
        self,
        command: (
            ManualSteerCommand
            | AllStopCommand
            | ClearFaultCommand
            | SendCommsMessageCommand
        ),
    ) -> None:
        """Dispatch operator commands.

        When ``NodeController`` is wired (ADR-024) the controller validates
        + applies the command; refusals are acked back over the WS. When
        not wired (sweep-only bench, no servo), the ADR-022 refusal stub
        semantics apply: log + ack ``command_refused`` loudly. B3 throughout.
        """
        if self._controller is not None:
            refusal = await self._controller.dispatch_command(command)
            if refusal is not None and self._command_channel is not None:
                refusal.setdefault("requestor_id", command.requestor_id)
                await self._command_channel.send_response(refusal)
            return

        kind = command.kind
        reason = "NodeController not wired on this node (sweep-only bench / no servo)."
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
