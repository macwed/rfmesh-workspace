"""Tests for ``NodeController`` (ADR-024 state machine + cancel-drained handoff)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from rfmesh_node.commands import AllStopCommand, ClearFaultCommand, ManualSteerCommand
from rfmesh_node.controller import (
    DRAIN_TIMEOUT_S,
    ManualSteerOutOfArcError,
    NodeController,
    NodeState,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RecordingServo:
    """Records every move call. Used to assert no-overlap invariants."""

    def __init__(self) -> None:
        self.moves: list[tuple[float, int, float]] = []  # (t_monotonic, axis, angle)

    def move(self, axis: int, angle_deg: float) -> None:
        self.moves.append((time.monotonic(), axis, angle_deg))


class _SlowFakeSweep:
    """Fake sweep loop that drives a long sequence of `servo.move` calls.

    Used to prove the cancel-drained handshake preempts BETWEEN moves
    rather than wedging or letting two writers interleave.
    """

    def __init__(self, servo: _RecordingServo, n_moves: int = 50) -> None:
        self._servo = servo
        self._n_moves = n_moves
        self.reset_count = 0
        self.observed_cancel_at: int | None = None

    async def run_once(
        self, stopping: asyncio.Event, cancel: asyncio.Event | None = None
    ) -> None:
        for i in range(self._n_moves):
            if stopping.is_set() or (cancel is not None and cancel.is_set()):
                self.observed_cancel_at = i
                return
            # Move BEFORE checking cancel again -- the next iteration
            # picks it up at the safe checkpoint.
            await asyncio.to_thread(self._servo.move, 0, float(i))
            await asyncio.sleep(0.001)

    def reset(self) -> None:
        self.reset_count += 1


class _WedgedFakeSweep:
    """Fake sweep loop that IGNORES cancel forever (simulates a buggy loop)."""

    def __init__(self) -> None:
        self.reset_count = 0

    async def run_once(
        self, stopping: asyncio.Event, cancel: asyncio.Event | None = None
    ) -> None:
        # Never honour cancel -- the controller's DRAIN_TIMEOUT_S should
        # promote the controller to FAULT.
        await asyncio.sleep(DRAIN_TIMEOUT_S * 2)

    def reset(self) -> None:
        self.reset_count += 1


# ---------------------------------------------------------------------------
# Plain dispatch unit tests (no controller-run task spawned)
# ---------------------------------------------------------------------------


def test_initial_state_picks_sweeping_when_sweep_loop_present() -> None:
    servo = _RecordingServo()
    sweep = _SlowFakeSweep(servo, n_moves=1)
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=sweep,  # type: ignore[arg-type]
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    assert c.state is NodeState.SWEEPING


def test_initial_state_picks_acquired_peer_when_rendezvous_present() -> None:
    servo = _RecordingServo()
    sweep = _SlowFakeSweep(servo, n_moves=1)
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=sweep,  # type: ignore[arg-type]
        rendezvous_loop=object(),  # type: ignore[arg-type]
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    assert c.state is NodeState.ACQUIRED_PEER


def test_initial_state_parked_when_no_loops() -> None:
    servo = _RecordingServo()
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=None,
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    assert c.state is NodeState.PARKED


@pytest.mark.asyncio
async def test_manual_steer_out_of_arc_refused_loudly() -> None:
    """B3: angle outside the calibrated arc is a loud refusal, not a clamp."""
    servo = _RecordingServo()
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=_SlowFakeSweep(servo),  # type: ignore[arg-type]
        rendezvous_loop=None,
        calibrated_arc_deg=(-30.0, 30.0),
        node_id="node-test",
    )
    cmd = ManualSteerCommand(axis=0, target_angle_deg=85.0)  # outside [-30, 30]
    refusal = await c.dispatch_command(cmd)
    assert refusal is not None
    assert refusal["kind"] == "command_refused"
    assert "outside the calibrated arc" in refusal["reason"]
    # State unchanged: no move issued, no MANUAL_HOLD started.
    assert c.state is NodeState.SWEEPING
    assert servo.moves == []


@pytest.mark.asyncio
async def test_manual_steer_refused_when_no_cal_arc_cached() -> None:
    """No arc -> every manual steer is refused with a specific reason."""
    servo = _RecordingServo()
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=_SlowFakeSweep(servo),  # type: ignore[arg-type]
        rendezvous_loop=None,
        calibrated_arc_deg=None,
        node_id="node-test",
    )
    cmd = ManualSteerCommand(axis=0, target_angle_deg=0.0)
    refusal = await c.dispatch_command(cmd)
    assert refusal is not None
    assert "no calibrated arc cached" in refusal["reason"]


@pytest.mark.asyncio
async def test_manual_steer_refused_in_fault_state() -> None:
    servo = _RecordingServo()
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=None,
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    # Force FAULT.
    c._state = NodeState.FAULT
    cmd = ManualSteerCommand(axis=0, target_angle_deg=0.0)
    refusal = await c.dispatch_command(cmd)
    assert refusal is not None
    assert "FAULT" in refusal["reason"]


@pytest.mark.asyncio
async def test_clear_fault_only_works_in_fault_state() -> None:
    servo = _RecordingServo()
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=None,
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    # Not in FAULT -> refused.
    refusal = await c.dispatch_command(ClearFaultCommand(requestor_id="ui"))
    assert refusal is not None
    assert refusal["refused_kind"] == "clear_fault"
    # Promote to FAULT then clear -> SWEEPING.
    c._state = NodeState.FAULT
    ok = await c.dispatch_command(ClearFaultCommand(requestor_id="ui"))
    assert ok is None
    assert c.state is NodeState.SWEEPING


@pytest.mark.asyncio
async def test_clear_fault_legacy_duck_typed_dispatch_still_works() -> None:
    """Backwards-compat: pre-typed legacy tests pass an object with .kind."""
    servo = _RecordingServo()
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=None,
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    c._state = NodeState.FAULT
    ok = await c.dispatch_command(type("CF", (), {"kind": "clear_fault"})())
    assert ok is None
    assert c.state is NodeState.SWEEPING


def test_validate_manual_angle_raises_typed_error() -> None:
    """ManualSteerOutOfArcError is the typed exception (same shape as PeerOutOfArcError)."""
    servo = _RecordingServo()
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=None,
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    with pytest.raises(ManualSteerOutOfArcError, match="outside the calibrated arc"):
        c._validate_manual_angle(100.0)


# ---------------------------------------------------------------------------
# Cancel-drained handshake regression tests (the binding gates from
# the council code-reviewer BLOCK on item 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_cancel_during_sweep_no_overlap() -> None:
    """The binding regression: a manual_steer arriving mid-sweep must not
    interleave its own servo.move with the sweep's still-in-flight moves.

    The fake sweep records every move it issues; the controller's
    cancel-drained handshake guarantees no controller-initiated move
    until the sweep has acknowledged drain.
    """
    servo = _RecordingServo()
    sweep = _SlowFakeSweep(servo, n_moves=20)
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=sweep,  # type: ignore[arg-type]
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(c.run(stopping))
    # Let the sweep get going.
    await asyncio.sleep(0.005)
    # Issue manual_steer mid-sweep.
    cmd = ManualSteerCommand(axis=0, target_angle_deg=12.5)
    refusal = await c.dispatch_command(cmd)
    assert refusal is None  # accepted

    # Wait for the MANUAL_HOLD mode to drive its single move.
    await asyncio.sleep(0.05)
    stopping.set()
    await asyncio.wait_for(task, timeout=2.0)

    # Sanity: the sweep must have observed the cancel BEFORE finishing
    # all 20 moves (otherwise the controller would have wedged).
    assert sweep.observed_cancel_at is not None
    assert sweep.observed_cancel_at < 20

    # The MANUAL_HOLD move (12.5) must appear, and it must be after the
    # last sweep move -- no interleaving.
    sweep_moves = [m for m in servo.moves if m[2] != 12.5]
    manual_moves = [m for m in servo.moves if m[2] == 12.5]
    assert manual_moves, "MANUAL_HOLD never issued its move"
    if sweep_moves:
        last_sweep_t = max(m[0] for m in sweep_moves)
        first_manual_t = min(m[0] for m in manual_moves)
        assert first_manual_t > last_sweep_t, (
            f"MANUAL_HOLD move at {first_manual_t} overlapped sweep "
            f"(last sweep at {last_sweep_t})"
        )

    # Sweep estimator must have been reset on mode exit (B3 stale guard).
    assert sweep.reset_count >= 1


@pytest.mark.asyncio
async def test_drain_timeout_promotes_to_fault() -> None:
    """A loop that ignores ``mode_cancel`` must NOT wedge the controller.

    DRAIN_TIMEOUT_S elapses, controller transitions to FAULT with a
    specific status_detail (B3 loud-not-silent).
    """
    servo = _RecordingServo()
    wedged = _WedgedFakeSweep()
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=wedged,  # type: ignore[arg-type]
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(c.run(stopping))
    await asyncio.sleep(0.05)  # let the wedged sweep start
    # Issue a manual_steer -- the wedged sweep ignores cancel, so the
    # switch_mode awaits DRAIN_TIMEOUT_S then -> FAULT.
    cmd = ManualSteerCommand(axis=0, target_angle_deg=0.0)
    refusal = await asyncio.wait_for(
        c.dispatch_command(cmd), timeout=DRAIN_TIMEOUT_S + 1.0
    )
    # The dispatch returned a refusal because switch_mode failed.
    assert refusal is not None
    assert "mode_drain_timeout" in refusal["reason"]
    assert c.state is NodeState.FAULT
    assert "mode_drain_timeout" in c.status_detail
    stopping.set()
    await asyncio.wait_for(task, timeout=DRAIN_TIMEOUT_S * 3)


@pytest.mark.asyncio
async def test_all_stop_parks_re_issuable() -> None:
    """ALL_STOP -> PARKED. Re-issuable: a subsequent manual_steer recovers."""
    servo = _RecordingServo()
    sweep = _SlowFakeSweep(servo, n_moves=5)
    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=sweep,  # type: ignore[arg-type]
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(c.run(stopping))
    await asyncio.sleep(0.005)
    # ALL_STOP.
    stop_cmd = AllStopCommand(requestor_id="ui-test")
    refusal = await c.dispatch_command(stop_cmd)
    assert refusal is None
    assert c.state is NodeState.PARKED
    # Manual steer recovers cleanly to MANUAL_HOLD.
    cmd = ManualSteerCommand(axis=0, target_angle_deg=10.0)
    refusal = await c.dispatch_command(cmd)
    assert refusal is None
    assert c.state is NodeState.MANUAL_HOLD
    stopping.set()
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_state_broadcast_fires_on_transition() -> None:
    """The on_state_change hook fires for every state transition."""
    servo = _RecordingServo()
    sweep = _SlowFakeSweep(servo, n_moves=5)
    pushed: list[dict[str, Any]] = []

    async def _push(payload: dict[str, Any]) -> bool:
        pushed.append(payload)
        return True

    c = NodeController(
        servo=servo,  # type: ignore[arg-type]
        sweep_loop=sweep,  # type: ignore[arg-type]
        rendezvous_loop=None,
        calibrated_arc_deg=(-90.0, 90.0),
        node_id="node-test",
        on_state_change=_push,
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(c.run(stopping))
    await asyncio.sleep(0.005)
    await c.dispatch_command(ManualSteerCommand(axis=0, target_angle_deg=0.0))
    await asyncio.sleep(0.01)
    stopping.set()
    await asyncio.wait_for(task, timeout=2.0)

    # At minimum: the SWEEPING -> MANUAL_HOLD transition + the post-dispatch
    # status_detail update on entering MANUAL_HOLD.
    states = [p.get("state") for p in pushed]
    assert NodeState.MANUAL_HOLD.value in states
