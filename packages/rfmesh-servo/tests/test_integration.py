"""Hardware-gated integration tests for the servo UART driver.

Skipped unless ``RFMESH_HARDWARE=1`` is set AND a probe finds an
ESP32-C3 servo controller on ``/dev/ttyACM*`` that answers ``PING``. CI
without hardware skips the entire module.

To run::

    RFMESH_HARDWARE=1 uv run pytest tests/scan/servo/test_integration.py -v

The ``test_calibration_persists_across_soft_reset`` case is the most
fragile: USB-CDC re-enumeration after ``RESET`` can change the device
path, so the test re-probes ``/dev/ttyACM*`` and skips if no controller
re-appears within a short grace window.
"""

from __future__ import annotations

import glob
import os
import time
from collections.abc import Iterator

import pytest
from rfmesh_servo.driver import ServoDriver
from rfmesh_servo.messages import NEVER_MOVED_SENTINEL, Calibration
from rfmesh_servo.transport import SerialTransport

_HW_GATE: bool = os.environ.get("RFMESH_HARDWARE") == "1"
_REENUM_GRACE_S: float = 2.5
_PROBE_TIMEOUT_S: float = 0.5


def _esp32_servo_available() -> str | None:
    """Return the first ``/dev/ttyACM*`` port that answers PING, or ``None``.

    Cheap when ``RFMESH_HARDWARE`` is not set: returns ``None`` immediately.
    When set, opens each candidate port and runs a short
    :meth:`ServoDriver.connect` (which validates the protocol version
    too). Failures of any kind cause the candidate to be skipped silently.
    """
    if not _HW_GATE:
        return None
    for port in sorted(glob.glob("/dev/ttyACM*")):
        try:
            transport = SerialTransport(port, baudrate=115200)
        except (ImportError, OSError):
            continue
        driver = ServoDriver(transport, reply_timeout_s=_PROBE_TIMEOUT_S, own_transport=True)
        try:
            driver.connect()
            return port
        except Exception:
            continue
        finally:
            driver.close()
    return None


_DETECTED_PORT: str | None = _esp32_servo_available()


pytestmark = [
    pytest.mark.skipif(not _HW_GATE, reason="RFMESH_HARDWARE=1 not set"),
    pytest.mark.skipif(
        _DETECTED_PORT is None,
        reason="ESP32-C3 servo controller not detected on /dev/ttyACM*",
    ),
]


@pytest.fixture
def servo_driver() -> Iterator[ServoDriver]:
    assert _DETECTED_PORT is not None  # guaranteed by skipif gate
    transport = SerialTransport(_DETECTED_PORT, baudrate=115200)
    driver = ServoDriver(transport, reply_timeout_s=_PROBE_TIMEOUT_S, own_transport=True)
    driver.connect()
    try:
        yield driver
    finally:
        driver.close()


class TestServoIntegration:
    """End-to-end tests that exercise a real ESP32-C3 + S2-T5a-firmware."""

    def test_ping_roundtrip(self, servo_driver: ServoDriver) -> None:
        pong = servo_driver.ping()
        assert pong.proto_version == 1
        assert pong.axis_count >= 1

    def test_move_centre_returns_within_500ms(self, servo_driver: ServoDriver) -> None:
        t0 = time.perf_counter()
        servo_driver.move(axis=0, angle_deg=0.0)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"MOVE ack took {elapsed * 1000:.0f} ms"

    def test_pos_sentinel_after_fresh_boot(self, servo_driver: ServoDriver) -> None:
        # Only meaningful if no MOVE has been issued since boot. If the
        # axis was already moved earlier in the session, just assert the
        # reply parses cleanly without the sentinel.
        pos = servo_driver.position(axis=0)
        if pos.ms_since_move == NEVER_MOVED_SENTINEL:
            assert pos.commanded_angle_deg == 0.0
        else:
            assert pos.ms_since_move >= 0

    def test_repeated_moves_dont_overflow(self, servo_driver: ServoDriver) -> None:
        # 100 small MOVEs verify the single-outstanding pipeline doesn't
        # wedge on stale frames after many round-trips.
        for i in range(100):
            angle = -10.0 + 0.2 * (i % 50)
            servo_driver.move(axis=0, angle_deg=angle)

    def test_calibration_persists_across_soft_reset(self, servo_driver: ServoDriver) -> None:
        original = servo_driver.get_calibration(axis=0)
        tweaked = Calibration(
            axis=0,
            pulse_min_us=600,
            pulse_max_us=2400,
            angle_min_deg=-80.0,
            angle_max_deg=80.0,
        )
        servo_driver.set_calibration(tweaked)
        servo_driver.persist_calibration(axis=0)
        servo_driver.reset()

        # USB-CDC re-enumeration: the device path may change.
        time.sleep(_REENUM_GRACE_S)
        new_port = _esp32_servo_available()
        if new_port is None:
            pytest.skip(
                "controller did not re-enumerate within grace window; manual reconnect required"
            )

        new_transport = SerialTransport(new_port, baudrate=115200)
        fresh_driver = ServoDriver(
            new_transport, reply_timeout_s=_PROBE_TIMEOUT_S, own_transport=True
        )
        try:
            fresh_driver.connect()
            after = fresh_driver.get_calibration(axis=0)
            assert after.pulse_min_us == 600
            assert after.pulse_max_us == 2400
            assert after.angle_min_deg == pytest.approx(-80.0)
            assert after.angle_max_deg == pytest.approx(80.0)
        finally:
            # Restore the original calibration on the fresh session before
            # closing — otherwise the next test sees the tweaked values.
            try:
                fresh_driver.set_calibration(original)
                fresh_driver.persist_calibration(axis=0)
            finally:
                fresh_driver.close()
