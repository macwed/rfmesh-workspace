"""``NodeController`` -- state machine + cancel-drained servo handoff.

ADR-024 (PROPOSED 2026-05-24). Lifts the council code-reviewer BLOCK on
item 2 of the post-cold-start plan (the rendezvous-vs-manual-steer race).

The class takes ownership of the servo-driving task. ``Node.run``
schedules ONE controller task that internally arbitrates between modes;
the previous ``_rendezvous_supervisor`` is folded into the controller
as one of its driving loops. There is therefore exactly one task that
may call ``servo.move()`` at any instant -- the single-writer
servo_uart_v1 invariant becomes a class invariant, not a comment.

State machine (5 states, ADR-024 §5):

* ``SWEEPING`` -- continuous L1 jammer-DF sweeps.
* ``ACQUIRED_PEER`` -- ADR-019 rendezvous time-share (acquire -> hold ->
  break for one DF sweep -> re-acquire). Only entered when rendezvous
  is enabled.
* ``MANUAL_HOLD`` -- operator-commanded angle; countdown to auto-resume
  ``SWEEPING`` after ``ManualSteerCommand.timeout_s``.
* ``PARKED`` -- mesh-wide ALL-STOP destination. Re-issuable: any
  ``manual_steer`` recovers to ``MANUAL_HOLD`` cleanly.
* ``FAULT`` -- unrecoverable-by-software (uncalibrated servo at boot,
  hardware refusal, drain timeout). Sticky; requires operator
  ``POST /node/{id}/clear_fault`` after fixing the underlying condition.

Cancel-drained handshake (ADR-024 §2 binding):

* ``_mode_cancel: asyncio.Event`` -- set by the controller when a new
  mode is requested. The currently-running loop checks it at each safe
  checkpoint (BETWEEN two ``servo.move`` calls, never DURING one).
* ``_mode_drained: asyncio.Event`` -- set by the loop in its ``finally:``
  on observing ``_mode_cancel`` so a raise still drains.
* ``switch_mode`` awaits ``_mode_drained`` with ``DRAIN_TIMEOUT_S``;
  on timeout, transitions to FAULT loudly (B3 -- a wedged loop must
  surface as FAULT rather than silently leave the controller stuck
  with the operator's slider unresponsive).
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .commands import (
    AllStopCommand,
    ClearFaultCommand,
    ManualSteerCommand,
    SendCommsMessageCommand,
)

if TYPE_CHECKING:
    from rfmesh_servo.driver import ServoDriver

    from .l1_sweep import L1SweepLoop
    from .rendezvous import RendezvousLoop

_LOG = logging.getLogger(__name__)


# Mode-handoff drain timeout. Sized as max(single servo-move worst-case
# budget * 2, ~2s). A single calibrated MG996R move is < 0.2s for the small
# steps the loops use (< 0.5s including settle); 2s gives ~4x headroom
# without making the operator wait visibly. Tunable in one place if the
# servo profile changes.
DRAIN_TIMEOUT_S: float = 2.0


class NodeState(enum.StrEnum):
    """Five operational states (ADR-024 §5). String enum so the value
    appears verbatim in WS frames + ``NodeStatus.status_detail`` + the
    UI badge CSS class names (link.css)."""

    SWEEPING = "sweeping"
    ACQUIRED_PEER = "acquired_peer"
    MANUAL_HOLD = "manual_hold"
    PARKED = "parked"
    FAULT = "fault"


class ManualSteerOutOfArcError(ValueError):
    """The operator commanded an angle outside the calibrated arc.

    Shape-mirrored on ``rendezvous.PeerOutOfArcError``: software-side
    refusal is loud (B3), never a silent clamp. Caught at the
    ``Node._handle_command`` boundary and acked over the WS as
    ``command_refused`` so ``link.html`` renders a red toast.
    """


class ControllerWedgedError(RuntimeError):
    """The drain-await timed out -- a loop ignored ``mode_cancel``.

    Promotes the controller to FAULT with a specific ``status_detail``
    so the operator sees the wedge instead of an unresponsive slider.
    """


# A type alias for the optional async hook the controller invokes to push
# a state-change frame back over the command-channel WS (so the UI sees
# the badge flip + countdown without polling).
StateBroadcastFn = Callable[[dict[str, object]], Awaitable[bool]]


class NodeController:
    """Owns the one servo-driving task + arbitrates between modes."""

    def __init__(
        self,
        *,
        servo: ServoDriver,
        sweep_loop: L1SweepLoop | None,
        rendezvous_loop: RendezvousLoop | None,
        calibrated_arc_deg: tuple[float, float] | None,
        node_id: str,
        on_state_change: StateBroadcastFn | None = None,
        comms_loop: object | None = None,
    ) -> None:
        """Bind dependencies.

        Args:
            servo: Connected ``ServoDriver``. Lifecycle owned by ``Node``.
            sweep_loop: Optional L1 jammer-DF loop. ``None`` disables SWEEPING
                fallback; the controller stays in PARKED when no peer is
                configured and no manual command has arrived.
            rendezvous_loop: Optional ADR-019 rendezvous loop. ``None`` means
                no peer configured -- the controller stays in SWEEPING.
            calibrated_arc_deg: ``(min, max)`` geographic-degree clamp for
                manual-steer angle validation (the same arc the UI clamps
                its slider to via ADR-022's ``node_hello``). ``None``
                disables manual steering -- ``dispatch_command`` refuses
                every ``manual_steer`` loudly with ``ManualSteerOutOfArcError``
                tagged as "no calibrated arc cached".
            node_id: For log + WS frame tagging.
            on_state_change: Optional async hook called whenever the state
                or ``manual_hold_expires_at`` changes; the controller passes
                the snapshot payload, the hook is typically
                ``CommandChannel.send_response`` so the backend caches the
                update and fans it to the UI.
        """
        self._servo = servo
        self._sweep_loop = sweep_loop
        self._rendezvous_loop = rendezvous_loop
        self._cal_arc = calibrated_arc_deg
        self._node_id = node_id
        self._on_state_change = on_state_change
        # ADR-025 Iter 4.5: optional CommsLoop. When wired, the
        # SendCommsMessageCommand dispatch path queues the payload onto
        # comms_loop's outbox; when None, the command refuses loudly
        # (B3 -- never silently drop a soldier message).
        self._comms_loop = comms_loop

        self._state: NodeState = (
            NodeState.ACQUIRED_PEER
            if rendezvous_loop is not None
            else NodeState.SWEEPING
            if sweep_loop is not None
            else NodeState.PARKED
        )
        self._status_detail: str = ""
        self._manual_hold_expires_at_ns: int | None = None
        self._manual_hold_target_deg: float | None = None
        # Tracks the firmware "current" angle so a MANUAL_HOLD ack can
        # surface where the antenna actually went. ``None`` until first
        # commanded.
        self._last_commanded_angle_deg: float | None = None

        # Cancel-drained handshake events. Recreated per mode-switch so a
        # stale set/clear cannot bleed across switches.
        self._mode_cancel = asyncio.Event()
        self._mode_drained = asyncio.Event()
        self._mode_drained.set()  # idle until first mode starts
        self._mode_lock = asyncio.Lock()  # serialises switch_mode calls
        self._running = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> NodeState:
        return self._state

    @property
    def status_detail(self) -> str:
        return self._status_detail

    @property
    def manual_hold_expires_at_ns(self) -> int | None:
        return self._manual_hold_expires_at_ns

    def snapshot(self) -> dict[str, object]:
        """JSON-serialisable snapshot for ``node_hello`` re-sends + WS pushes."""
        return {
            "node_id": self._node_id,
            "state": self._state.value,
            "status_detail": self._status_detail,
            "manual_hold_expires_at_ns": self._manual_hold_expires_at_ns,
            "last_commanded_angle_deg": self._last_commanded_angle_deg,
            "controller_ready": True,
        }

    # ------------------------------------------------------------------
    # Mode-switch protocol (ADR-024 §2)
    # ------------------------------------------------------------------

    async def _switch_mode(self, new_mode: NodeState) -> bool:
        """Drain the current loop, reset estimators, transition to ``new_mode``.

        Returns True on clean handoff. On drain timeout, promotes to FAULT
        and returns False (B3 -- a wedged loop must surface loudly).
        """
        async with self._mode_lock:
            self._mode_cancel.set()
            try:
                await asyncio.wait_for(self._mode_drained.wait(), timeout=DRAIN_TIMEOUT_S)
            except TimeoutError:
                prior = self._state.value
                self._state = NodeState.FAULT
                self._status_detail = f"mode_drain_timeout: prior_mode={prior}"
                _LOG.error(
                    "NodeController %s: drain timed out leaving %s; -> FAULT",
                    self._node_id,
                    prior,
                )
                await self._broadcast_state_change()
                return False
            # Drain ack received -> reset event pair + estimator state.
            self._mode_cancel.clear()
            self._mode_drained.clear()
            self._reset_loop_estimators()
            self._state = new_mode
            await self._broadcast_state_change()
            return True

    def _reset_loop_estimators(self) -> None:
        """B3 stale-accumulator guard (ADR-024 §3)."""
        if self._sweep_loop is not None:
            self._sweep_loop.reset()
        if self._rendezvous_loop is not None:
            self._rendezvous_loop.reset()

    async def _broadcast_state_change(self) -> None:
        if self._on_state_change is None:
            return
        with contextlib.suppress(Exception):
            await self._on_state_change({"kind": "node_state", **self.snapshot()})

    # ------------------------------------------------------------------
    # Operator command dispatch (called by Node._handle_command).
    # ------------------------------------------------------------------

    async def dispatch_command(
        self,
        command: ManualSteerCommand | AllStopCommand | ClearFaultCommand | object,
    ) -> dict[str, object] | None:
        """Apply an operator command; return a refusal payload or ``None`` on ok.

        Returns a ``command_refused``-shaped dict when refused so the caller
        can ack the UI; returns ``None`` when the command was accepted and
        the state transition is in flight.
        """
        if isinstance(command, ManualSteerCommand):
            return await self._dispatch_manual_steer(command)
        if isinstance(command, AllStopCommand):
            return await self._dispatch_all_stop(command)
        if isinstance(command, ClearFaultCommand):
            return self._dispatch_clear_fault(command)
        if isinstance(command, SendCommsMessageCommand):
            return self._dispatch_send_comms_message(command)
        # Legacy duck-typed fallback (used by older test fixtures).
        if getattr(command, "kind", None) == "clear_fault":
            return self._dispatch_clear_fault(command)
        return {
            "kind": "command_refused",
            "node_id": self._node_id,
            "refused_kind": getattr(command, "kind", "unknown"),
            "reason": f"unknown command kind {getattr(command, 'kind', None)!r}",
        }

    def _dispatch_send_comms_message(
        self,
        command: SendCommsMessageCommand,
    ) -> dict[str, object] | None:
        """Route a soldier message to the comms loop's TX outbox (ADR-025 Iter 4.5).

        Refuses loudly (B3) when:
        * No comms loop is wired (this node is DF-mode or sweep-only).
        * The node is in FAULT (operator must clear the fault first).
        * The payload exceeds the comms config's max-payload bound
          (``queue_outbound`` itself raises ``ValueError``; we wrap
          into ``command_refused`` so the operator sees the reason).
        """
        if self._state is NodeState.FAULT:
            return self._refused(
                command,
                "node is FAULT; POST /node/{id}/clear_fault before sending comms messages",
            )
        loop = self._comms_loop
        if loop is None:
            return self._refused(
                command,
                "comms loop not wired on this node (DF-mode or sweep-only -- "
                "operator must reconfigure YAML with a comms: block)",
            )
        try:
            loop.queue_outbound(command.payload_bytes())  # type: ignore[attr-defined]
        except ValueError as exc:
            return self._refused(command, str(exc))
        return None

    async def _dispatch_manual_steer(self, command: ManualSteerCommand) -> dict[str, object] | None:
        if self._state is NodeState.FAULT:
            return self._refused(
                command,
                "node is FAULT; POST /node/{id}/clear_fault after fixing the underlying condition",
            )
        # Validate angle against the cached calibrated arc -- LOUD, NOT CLAMP (B3).
        try:
            self._validate_manual_angle(command.target_angle_deg)
        except ManualSteerOutOfArcError as exc:
            return self._refused(command, str(exc))

        target = command.target_angle_deg
        if not await self._switch_mode(NodeState.MANUAL_HOLD):
            return self._refused(command, self._status_detail)
        # The new mode is in flight; we set the hold metadata + record
        # the commanded angle. The mode loop (run()) reads
        # _manual_hold_target_deg and drives the servo.
        self._manual_hold_target_deg = target
        self._manual_hold_expires_at_ns = time.time_ns() + int(command.timeout_s * 1e9)
        self._last_commanded_angle_deg = target
        self._status_detail = f"MANUAL_HOLD @ {target:.1f} deg, timeout {command.timeout_s:.1f}s"
        await self._broadcast_state_change()
        return None

    async def _dispatch_all_stop(self, command: AllStopCommand) -> dict[str, object] | None:
        if not await self._switch_mode(NodeState.PARKED):
            return self._refused(command, self._status_detail)
        self._status_detail = f"PARKED by ALL_STOP from {command.requestor_id}"
        self._manual_hold_expires_at_ns = None
        self._manual_hold_target_deg = None
        await self._broadcast_state_change()
        return None

    def _dispatch_clear_fault(self, command: object) -> dict[str, object] | None:
        if self._state is not NodeState.FAULT:
            return {
                "kind": "command_refused",
                "node_id": self._node_id,
                "refused_kind": "clear_fault",
                "reason": f"not in FAULT (current state: {self._state.value})",
            }
        # Operator ack: transition to SWEEPING (the safe default). If the
        # underlying condition is still present, the next loop tick will
        # raise FAULT again with the new status_detail.
        self._state = NodeState.SWEEPING
        self._status_detail = "FAULT cleared by operator; resuming SWEEPING"
        return None

    def _validate_manual_angle(self, angle_deg: float) -> None:
        if self._cal_arc is None:
            msg = (
                f"NODE-{self._node_id}: no calibrated arc cached; refusing manual_steer "
                "until node_hello reports a calibration. Re-survey the mount or "
                "run rfmesh-servo-calibrate."
            )
            raise ManualSteerOutOfArcError(msg)
        lo, hi = self._cal_arc
        if not (lo <= angle_deg <= hi):
            msg = (
                f"NODE-{self._node_id}: requested {angle_deg:.1f} deg is outside the "
                f"calibrated arc [{lo:.1f}, {hi:.1f}]. Re-survey the mount, "
                "or POST /node/{id}/clear_fault if the arc is wrong."
            )
            raise ManualSteerOutOfArcError(msg)

    def _refused(
        self,
        command: ManualSteerCommand | AllStopCommand | SendCommsMessageCommand,
        reason: str,
    ) -> dict[str, object]:
        return {
            "kind": "command_refused",
            "node_id": self._node_id,
            "refused_kind": command.kind,
            "requestor_id": command.requestor_id,
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Controller task -- one per Node, dispatches mode loops sequentially.
    # ------------------------------------------------------------------

    async def run(self, stopping: asyncio.Event) -> None:
        """Drive whichever mode is current; switch on cancel-drained handoff.

        Holds the servo-driving task. Exactly one ``servo.move()`` writer
        at any instant (the loop or move it dispatches to); the mode-switch
        handshake guarantees no overlap between modes.
        """
        if self._running:
            msg = "NodeController.run called twice; create a new instance."
            raise RuntimeError(msg)
        self._running = True
        _LOG.info("NodeController %s: starting in %s", self._node_id, self._state.value)
        try:
            while not stopping.is_set():
                self._mode_drained.clear()  # mode is now "active"
                try:
                    await self._run_current_mode(stopping)
                finally:
                    # B3: drain ack must fire even on raise so switch_mode
                    # never wedges (the controller's TimeoutError->FAULT
                    # path handles the case where this finally is missed
                    # entirely, e.g. by a future contributor's bug).
                    self._mode_drained.set()
                if stopping.is_set():
                    break
                # Yield explicitly so a pending ``switch_mode`` awaiter
                # (the operator dispatch task) gets to wake up and apply
                # its state change BEFORE we iterate into another
                # _run_current_mode under the old state. Without this
                # yield, a mode loop that returns immediately on a
                # preset ``_mode_cancel`` spins this while loop without
                # ever giving the awaiter a turn -- deadlock the
                # controller-test surface caught.
                await asyncio.sleep(0)
        finally:
            self._running = False

    async def _run_current_mode(self, stopping: asyncio.Event) -> None:
        """Drive the current mode for one "shift" then yield to the switch."""
        state = self._state
        if state is NodeState.SWEEPING:
            await self._run_sweeping(stopping)
        elif state is NodeState.ACQUIRED_PEER:
            await self._run_acquired_peer(stopping)
        elif state is NodeState.MANUAL_HOLD:
            await self._run_manual_hold(stopping)
        elif state is NodeState.PARKED:
            await self._run_parked(stopping)
        elif state is NodeState.FAULT:
            await self._run_fault(stopping)

    async def _run_sweeping(self, stopping: asyncio.Event) -> None:
        if self._sweep_loop is None:
            # No L1 path -> downgrade to PARKED quietly (still loud via
            # status_detail). This should only happen on a misconfigured
            # node, since the controller's initial-state logic picks
            # PARKED when no sweep loop is provided.
            self._state = NodeState.PARKED
            self._status_detail = "no sweep loop configured; PARKED"
            return
        await self._sweep_loop.run_once(stopping, self._mode_cancel)

    async def _run_acquired_peer(self, stopping: asyncio.Event) -> None:
        rv = self._rendezvous_loop
        if rv is None:
            # No peer -> back to SWEEPING.
            self._state = NodeState.SWEEPING
            return
        locked = await rv.acquire(stopping, self._mode_cancel)
        if not locked:
            # FAILED rendezvous: do a useful jammer sweep, then retry.
            if self._sweep_loop is not None and not self._mode_cancel.is_set():
                await self._sweep_loop.run_once(stopping, self._mode_cancel)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._mode_cancel.wait(), timeout=rv.config.retry_pause_s)
            return
        # LOCKED: hold the link, then break for one jammer sweep, re-acquire.
        await rv.hold(stopping, rv.config.link_hold_s, self._mode_cancel)
        if self._sweep_loop is not None and not self._mode_cancel.is_set():
            await self._sweep_loop.run_once(stopping, self._mode_cancel)

    async def _run_manual_hold(self, stopping: asyncio.Event) -> None:
        target = self._manual_hold_target_deg
        if target is not None:
            await asyncio.to_thread(self._servo.move, 0, target)
            self._manual_hold_target_deg = None  # one move per dispatch
        # Wait for whichever comes first: timeout, mode_cancel, or shutdown.
        expires_at = self._manual_hold_expires_at_ns
        if expires_at is None:
            timeout_s = 1.0  # nothing to wait on; check soon
        else:
            remaining_ns = expires_at - time.time_ns()
            timeout_s = max(0.1, remaining_ns / 1e9)
        fired = await self._wait_cancel_or_stop(stopping, timeout_s)
        if fired == "timeout":
            # MANUAL_HOLD expired -> resume SWEEPING (or ACQUIRED_PEER if
            # rendezvous is configured).
            self._manual_hold_expires_at_ns = None
            self._status_detail = "MANUAL_HOLD expired; resuming"
            self._state = (
                NodeState.ACQUIRED_PEER if self._rendezvous_loop is not None else NodeState.SWEEPING
            )
            await self._broadcast_state_change()

    async def _run_parked(self, stopping: asyncio.Event) -> None:
        # PARKED is a wait state: yield until a command (-> mode_cancel) or
        # shutdown. The status_detail is set by the dispatch path.
        await self._wait_cancel_or_stop(stopping, timeout_s=5.0)

    async def _run_fault(self, stopping: asyncio.Event) -> None:
        # FAULT is sticky. Wait for a clear_fault command (-> mode_cancel)
        # or shutdown. status_detail carries the loud reason.
        await self._wait_cancel_or_stop(stopping, timeout_s=5.0)

    async def _wait_cancel_or_stop(self, stopping: asyncio.Event, timeout_s: float) -> str:
        """Wait for whichever fires first: mode_cancel, stopping, or timeout.

        Returns ``"cancel"`` / ``"stopping"`` / ``"timeout"`` so the caller can
        branch. Keeps every wait-state mode shutdown-responsive (the test
        suite's ``asyncio.wait_for(task, timeout=2.0)`` cannot wait for a
        10 s MANUAL_HOLD).
        """
        cancel_t = asyncio.create_task(self._mode_cancel.wait())
        stop_t = asyncio.create_task(stopping.wait())
        try:
            done, _pending = await asyncio.wait(
                {cancel_t, stop_t},
                timeout=timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (cancel_t, stop_t):
                if not t.done():
                    t.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await t
        if cancel_t in done:
            return "cancel"
        if stop_t in done:
            return "stopping"
        return "timeout"


__all__ = [
    "DRAIN_TIMEOUT_S",
    "ControllerWedgedError",
    "ManualSteerOutOfArcError",
    "NodeController",
    "NodeState",
    "StateBroadcastFn",
]
