"""Tests for antenna rendezvous (ADR-019) -- all hardware-free.

Covers the pure helpers (geodesic bearing, role split, out-of-arc refusal),
the connection-mode loop (bidirectional pointing, mutual-lock convergence,
loud failure on no peer peak), and the Node-level servo ownership (one
connect/close, status surfaced).
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from rfmesh_contracts import GeodeticPosition
from rfmesh_node import Node
from rfmesh_node.rendezvous import (
    DuplicateNodeIdError,
    PeerOutOfArcError,
    RendezvousConfig,
    RendezvousLoop,
    expected_servo_angle,
    geodesic_initial_bearing_deg,
    role,
)

# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------


class _RecordingServo:
    """Records connect/close counts and the full move history."""

    def __init__(self) -> None:
        self.connects = 0
        self.closes = 0
        self.last_angle = 0.0
        self.angles: list[float] = []

    def connect(self) -> None:
        self.connects += 1

    def move(self, axis: int, angle_deg: float) -> None:
        assert axis == 0
        self.last_angle = angle_deg
        self.angles.append(angle_deg)

    def stop(self, axis: int) -> None:  # pragma: no cover - unused
        pass

    def close(self) -> None:
        self.closes += 1


class _Caps:
    driver = "sim"
    n_coherent_channels = 1
    actual_sample_rate_hz = 2_400_000.0
    is_power_calibrated = False


class _BeamReceiver:
    """Single-channel IQ: a Gaussian RSSI beam peaked when the servo sits at
    ``peak_angle_deg``. ``present=False`` returns noise only (no emitter).

    Implements the full ``Receiver`` surface so it also works behind ``Node``.
    """

    def __init__(
        self,
        servo: _RecordingServo,
        peak_angle_deg: float,
        *,
        snr_db: float = 30.0,
        hpbw_deg: float = 50.0,
        present: bool = True,
    ) -> None:
        self._servo = servo
        self._peak = peak_angle_deg
        self._snr_db = snr_db
        self._sigma = hpbw_deg / 2.3548
        self._present = present
        self._rng = np.random.default_rng(0)
        self.open_called = 0
        self.close_called = 0

    def open(self) -> None:
        self.open_called += 1

    def configure(self, config: object) -> None:
        pass

    def capabilities(self) -> _Caps:
        return _Caps()

    def close(self) -> None:
        self.close_called += 1

    def read(self, n: int) -> np.ndarray:
        noise = (self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n)) / np.sqrt(2.0)
        if not self._present:
            return noise.astype(np.complex64)
        miss = self._servo.last_angle - self._peak
        gain = float(np.exp(-0.5 * (miss / self._sigma) ** 2))
        amp = np.sqrt(10.0 ** (self._snr_db / 10.0)) * gain
        tone = amp * np.exp(1j * 2.0 * np.pi * 0.01 * np.arange(n))
        return (tone + noise).astype(np.complex64)


class _FakeBearer:
    def __init__(self) -> None:
        self.statuses: list[object] = []

    def send_bearing(self, report: object) -> None:  # pragma: no cover - unused
        pass

    def send_status(self, status: object) -> None:
        self.statuses.append(status)

    def receive(self) -> tuple[()]:  # pragma: no cover - unused
        return ()

    def close(self) -> None:  # pragma: no cover - unused
        pass


def _angle_err(measured: float, truth: float) -> float:
    return abs((measured - truth + 180.0) % 360.0 - 180.0)


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dlat", "dlon", "expected"),
    [
        (1.0, 0.0, 0.0),  # due north
        (0.0, 1.0, 90.0),  # due east
        (-1.0, 0.0, 180.0),  # due south
        (0.0, -1.0, 270.0),  # due west
    ],
)
def test_geodesic_initial_bearing_cardinal(dlat: float, dlon: float, expected: float) -> None:
    origin = GeodeticPosition(lat_deg=0.0, lon_deg=0.0, hae_m=0.0, sigma_m=1.0)
    target = GeodeticPosition(lat_deg=dlat, lon_deg=dlon, hae_m=0.0, sigma_m=1.0)
    assert _angle_err(geodesic_initial_bearing_deg(origin, target), expected) < 0.5


def test_role_split_is_total_and_opposite() -> None:
    assert role("node-a", "node-b") == "STARER"
    assert role("node-b", "node-a") == "SCANNER"
    # Opposite for any ordering -> the two nodes never both SCAN.
    assert role("node-a", "node-b") != role("node-b", "node-a")


def test_role_duplicate_id_raises() -> None:
    with pytest.raises(DuplicateNodeIdError, match="unique"):
        role("node-x", "node-x")


def test_expected_servo_angle_in_arc() -> None:
    self_pos = GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=5.0)
    peer_pos = GeodeticPosition(lat_deg=50.0, lon_deg=5.05, hae_m=0.0, sigma_m=5.0)  # east
    angle = expected_servo_angle(self_pos, peer_pos, 90.0, min_servo_deg=-90.0, max_servo_deg=90.0)
    assert abs(angle) < 1.0  # boresight 90 (east) -> servo ~0


def test_expected_servo_angle_out_of_arc_refuses() -> None:
    self_pos = GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=5.0)
    peer_pos = GeodeticPosition(lat_deg=49.0, lon_deg=5.0, hae_m=0.0, sigma_m=5.0)  # south
    # Peer due south, boresight north -> 180 deg behind, outside ±90.
    with pytest.raises(PeerOutOfArcError, match="outside"):
        expected_servo_angle(self_pos, peer_pos, 0.0, min_servo_deg=-90.0, max_servo_deg=90.0)


# ---------------------------------------------------------------------------
# Connection-mode loop.
# ---------------------------------------------------------------------------


def _loop(
    *,
    node_id: str,
    peer_id: str,
    self_pos: GeodeticPosition,
    peer_pos: GeodeticPosition,
    boresight: float,
    servo: _RecordingServo,
    receiver: _BeamReceiver,
) -> RendezvousLoop:
    return RendezvousLoop(
        receiver=receiver,
        servo=servo,
        node_id=node_id,
        node_position=self_pos,
        boresight_heading_deg=boresight,
        config=RendezvousConfig(
            peer_node_id=peer_id,
            peer_position=peer_pos,
            settle_s=0.0,
            dwell_samples=256,
            refine_half_arc_deg=20.0,
            refine_step_deg=2.0,
        ),
    )


async def test_scanner_points_directly_and_stays_in_arc() -> None:
    """SCANNER points at the GPS-prior bearing first (no same-side park), refines,
    and never commands outside ±90° (connection mode is bidirectional)."""
    self_pos = GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=5.0)
    peer_pos = GeodeticPosition(lat_deg=50.0, lon_deg=5.02, hae_m=0.0, sigma_m=5.0)  # east
    servo = _RecordingServo()
    boresight = 70.0  # peer bearing ~90 -> target servo ~20
    receiver = _BeamReceiver(servo, peak_angle_deg=20.0)
    loop = _loop(
        node_id="node-z",
        peer_id="node-a",  # self > peer -> SCANNER
        self_pos=self_pos,
        peer_pos=peer_pos,
        boresight=boresight,
        servo=servo,
        receiver=receiver,
    )
    assert loop.role == "SCANNER"
    target = loop.target_servo_angle()

    locked = await loop.acquire(asyncio.Event())

    assert locked
    # First move is the direct GPS-prior point, NOT a park at the arc edge.
    assert servo.angles[0] == pytest.approx(target, abs=1e-6)
    assert target != pytest.approx(-90.0)
    # Bidirectional + arc-limited: nothing outside ±90.
    assert all(-90.0 <= a <= 90.0 for a in servo.angles)
    assert loop.last_report is not None
    assert _angle_err(loop.last_report.azimuth_deg, 90.0) < 5.0
    assert loop.last_report.azimuth_sigma_deg > 0.0


async def test_mutual_lock_convergence() -> None:
    """Two nodes pointing at each other both lock; the SCANNER recovers the
    reciprocal bearing with an honest sigma."""
    pos_a = GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=5.0)
    pos_b = GeodeticPosition(lat_deg=50.0, lon_deg=5.02, hae_m=0.0, sigma_m=5.0)

    servo_a = _RecordingServo()
    servo_b = _RecordingServo()
    # A boresight east (toward B) -> target ~0; B boresight west (toward A) -> target ~0.
    loop_a = _loop(
        node_id="node-a",
        peer_id="node-b",
        self_pos=pos_a,
        peer_pos=pos_b,
        boresight=90.0,
        servo=servo_a,
        receiver=_BeamReceiver(servo_a, peak_angle_deg=0.0),
    )
    loop_b = _loop(
        node_id="node-b",
        peer_id="node-a",
        self_pos=pos_b,
        peer_pos=pos_a,
        boresight=270.0,
        servo=servo_b,
        receiver=_BeamReceiver(servo_b, peak_angle_deg=0.0),
    )
    assert loop_a.role == "STARER"
    assert loop_b.role == "SCANNER"

    locked_a = await loop_a.acquire(asyncio.Event())
    locked_b = await loop_b.acquire(asyncio.Event())

    assert locked_a and locked_b
    assert loop_b.last_report is not None
    # B measures the reciprocal bearing back to A (~270 deg true).
    assert _angle_err(loop_b.last_report.azimuth_deg, 270.0) < 5.0
    assert loop_b.last_report.azimuth_sigma_deg > 0.0


async def test_scanner_fails_loudly_with_no_peer_peak() -> None:
    """No emitter -> refine ladder exhausts -> acquire False, loud status (B3)."""
    self_pos = GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=5.0)
    peer_pos = GeodeticPosition(lat_deg=50.0, lon_deg=5.02, hae_m=0.0, sigma_m=5.0)
    servo = _RecordingServo()
    receiver = _BeamReceiver(servo, peak_angle_deg=0.0, present=False)  # noise only
    loop = _loop(
        node_id="node-z",
        peer_id="node-a",
        self_pos=self_pos,
        peer_pos=peer_pos,
        boresight=90.0,
        servo=servo,
        receiver=receiver,
    )

    locked = await loop.acquire(asyncio.Event())

    assert not locked
    assert "no peer peak" in loop.last_status


async def test_out_of_arc_fails_loudly() -> None:
    """Peer behind boresight -> acquire refuses before moving (B3)."""
    self_pos = GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=5.0)
    peer_pos = GeodeticPosition(lat_deg=49.0, lon_deg=5.0, hae_m=0.0, sigma_m=5.0)  # south
    servo = _RecordingServo()
    loop = _loop(
        node_id="node-z",
        peer_id="node-a",
        self_pos=self_pos,
        peer_pos=peer_pos,
        boresight=0.0,  # facing north, peer due south
        servo=servo,
        receiver=_BeamReceiver(servo, peak_angle_deg=0.0),
    )

    locked = await loop.acquire(asyncio.Event())

    assert not locked
    assert "outside" in loop.last_status
    assert servo.angles == []  # refused before any motion


# ---------------------------------------------------------------------------
# Node-level servo ownership.
# ---------------------------------------------------------------------------


async def test_node_owns_servo_lifecycle_under_rendezvous(
    l1_node_config: object,
    node_position: GeodeticPosition,
) -> None:
    """Node connects the servo once and closes it once; the supervisor runs and
    surfaces rendezvous status via NodeStatus.status_detail."""
    # Peer due north of the node (config heading 0) -> target servo ~0, in-arc.
    peer_pos = node_position.model_copy(update={"lat_deg": node_position.lat_deg + 0.01})
    servo = _RecordingServo()
    receiver = _BeamReceiver(servo, peak_angle_deg=0.0)
    bearer = _FakeBearer()
    rv = RendezvousLoop(
        receiver=receiver,
        servo=servo,
        node_id=l1_node_config.node_id,  # "node-l1-test" < "node-z" -> STARER
        node_position=node_position,
        boresight_heading_deg=0.0,
        config=RendezvousConfig(
            peer_node_id="node-z",
            peer_position=peer_pos,
            settle_s=0.0,
            dwell_samples=256,
            link_hold_s=0.02,
            retry_pause_s=0.02,
        ),
    )
    node = Node(l1_node_config, receiver=receiver, bearer=bearer, rendezvous_loop=rv, servo=servo)

    runner = asyncio.create_task(node.run())
    try:
        await asyncio.sleep(0.15)
    finally:
        await node.shutdown()
        await asyncio.wait_for(runner, timeout=2.0)

    assert servo.connects == 1, "Node must connect the servo exactly once"
    assert servo.closes == 1, "Node must close the servo exactly once"
    assert servo.angles, "supervisor should have pointed the servo"
    assert all(-90.0 <= a <= 90.0 for a in servo.angles)
    assert any("STARER holding" in s.status_detail for s in bearer.statuses)
    # No leaked Node tasks.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert not any((t.get_name() or "").startswith("node-") for t in pending)
