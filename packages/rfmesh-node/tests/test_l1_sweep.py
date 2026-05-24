"""L1SweepLoop: drives a fake servo + fake receiver -> real estimator -> bearer.

Hardware-free: a fake rig couples the commanded servo angle to a synthetic
RSSI beam, so the real ``L1AmplitudeSweepEstimator`` recovers a bearing
close to the injected emitter and the loop ships it over the bearer.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from rfmesh_contracts import BearingReport, GeodeticPosition
from rfmesh_node.l1_sweep import L1SweepConfig, L1SweepLoop

_POS = GeodeticPosition(lat_deg=50.33, lon_deg=5.0, hae_m=200.0, sigma_m=5.0)
_BORESIGHT = 90.0
_TRUE_OFFSET = 10.0  # emitter 10 deg right of boresight -> truth azimuth 100
_DWELL = 256


class _FakeServo:
    """Records the last commanded angle; that drives the fake receiver's beam."""

    def __init__(self) -> None:
        self.last_angle: float = 0.0
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def move(self, axis: int, angle_deg: float) -> None:
        assert axis == 0
        self.last_angle = angle_deg

    def close(self) -> None:
        self.closed = True


class _FakeReceiver:
    """Synthetic single-channel IQ: Gaussian beam centred on the emitter."""

    def __init__(self, servo: _FakeServo, *, snr_db: float = 30.0) -> None:
        self._servo = servo
        self._snr_db = snr_db
        self._rng = np.random.default_rng(0)

    def read(self, n: int) -> np.ndarray:
        sigma = 50.0 / 2.3548  # 50 deg HPBW -> Gaussian sigma
        miss = self._servo.last_angle - _TRUE_OFFSET
        gain = float(np.exp(-0.5 * (miss / sigma) ** 2))
        amp = np.sqrt(10.0 ** (self._snr_db / 10.0)) * gain
        noise = (self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n)) / np.sqrt(2.0)
        tone = amp * np.exp(1j * 2.0 * np.pi * 0.01 * np.arange(n))
        return (tone + noise).astype(np.complex64)


class _FakeBearer:
    def __init__(self) -> None:
        self.sent: list[BearingReport] = []

    def send_bearing(self, report: BearingReport) -> None:
        self.sent.append(report)

    def send_status(self, status: object) -> None:  # pragma: no cover - unused here
        pass

    def receive(self) -> tuple[()]:  # pragma: no cover - unused here
        return ()

    def close(self) -> None:  # pragma: no cover - unused here
        pass


async def test_sweep_loop_emits_and_sends_a_bearing() -> None:
    servo = _FakeServo()
    receiver = _FakeReceiver(servo)
    bearer = _FakeBearer()
    loop = L1SweepLoop(
        receiver=receiver,
        bearer=bearer,
        servo=servo,
        node_id="node-rtl-01",
        node_position=_POS,
        boresight_heading_deg=_BORESIGHT,
        config=L1SweepConfig(
            min_deg=-45.0,
            max_deg=45.0,
            step_deg=3.0,
            settle_s=0.0,
            dwell_samples=_DWELL,
            inter_sweep_s=0.02,
        ),
    )

    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    try:
        for _ in range(200):  # up to ~10 s
            if bearer.sent:
                break
            await asyncio.sleep(0.05)
    finally:
        stopping.set()
        await asyncio.wait_for(task, timeout=5.0)

    # Servo connect/close is owned by Node, not L1SweepLoop (ADR-019); this
    # unit test drives the loop directly, so it does not exercise lifecycle.
    assert bearer.sent, "loop produced no bearing"
    report = bearer.sent[0]
    truth = (_BORESIGHT + _TRUE_OFFSET) % 360.0
    err = (report.azimuth_deg - truth + 180.0) % 360.0 - 180.0
    assert abs(err) < 5.0, f"bearing {report.azimuth_deg} too far from truth {truth} (err {err})"
    assert report.azimuth_sigma_deg > 0.0
    assert report.node_id == "node-rtl-01"


async def test_sweep_loop_hold_on_peak_parks_servo_and_resends() -> None:
    """hold_on_peak: after the first bearing, servo parks at peak and
    subsequent iterations re-emit the same bearing with refreshed
    t_unix_ns (no new sweeps)."""
    servo = _FakeServo()
    receiver = _FakeReceiver(servo)
    bearer = _FakeBearer()
    loop = L1SweepLoop(
        receiver=receiver,
        bearer=bearer,
        servo=servo,
        node_id="node-rtl-01",
        node_position=_POS,
        boresight_heading_deg=_BORESIGHT,
        config=L1SweepConfig(
            min_deg=-45.0,
            max_deg=45.0,
            step_deg=3.0,
            settle_s=0.0,
            dwell_samples=_DWELL,
            inter_sweep_s=0.02,
            hold_on_peak=True,
        ),
    )

    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    try:
        # Wait for at least 3 emissions (1 sweep + 2 resends).
        for _ in range(400):
            if len(bearer.sent) >= 3:
                break
            await asyncio.sleep(0.05)
    finally:
        stopping.set()
        await asyncio.wait_for(task, timeout=5.0)

    assert len(bearer.sent) >= 3, f"only {len(bearer.sent)} bearings emitted"
    # All emissions agree on the same azimuth (single peak, held).
    azs = {round(r.azimuth_deg, 2) for r in bearer.sent}
    assert len(azs) == 1, f"azimuth drifted across resends: {azs}"
    # Timestamps strictly increase (re-emission with refreshed t_unix_ns).
    ts = [r.t_unix_ns for r in bearer.sent]
    assert ts == sorted(ts) and len(set(ts)) == len(ts), (
        f"resend timestamps not strictly increasing: {ts}"
    )
    # Servo's last commanded angle == peak's servo-frame angle (within step).
    peak_az = bearer.sent[0].azimuth_deg
    expected_servo = (peak_az - _BORESIGHT + 180.0) % 360.0 - 180.0
    assert abs(servo.last_angle - expected_servo) < 4.0, (
        f"servo at {servo.last_angle}, expected near {expected_servo}"
    )


def test_sweep_config_rejects_too_narrow_arc() -> None:
    # 3 angles (-2,0,2) < 7 needed for the parabola fit -> loud refusal.
    with pytest.raises(ValueError, match="needs >="):
        L1SweepLoop(
            receiver=_FakeReceiver(_FakeServo()),
            bearer=None,
            servo=_FakeServo(),
            node_id="n",
            node_position=_POS,
            boresight_heading_deg=_BORESIGHT,
            config=L1SweepConfig(min_deg=-2.0, max_deg=2.0, step_deg=2.0, dwell_samples=_DWELL),
        )
