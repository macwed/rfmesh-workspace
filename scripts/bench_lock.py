"""bench_lock.py — minimal "sweep, find peak, park servo, stay" demo.

One node, one terminal, no backend, no UI, no bearer. Proves the
scan-and-lock primitive: each node independently sweeps, picks the
strongest RSSI peak in its arc, points the servo at it, and stays
pointing forever (until Ctrl-C).

Run on a laptop with one RTL-SDR V4 + one calibrated ESP32-C6 servo
controller + ATK-10 Yagi:

    uv run python scripts/bench_lock.py \\
        --servo-port /dev/ttyACM0 \\
        --sdr-serial 00000001 \\
        --center-freq-hz 915.0e6 \\
        --gain-db 30 \\
        --heading-deg 90 \\
        --sweep-min-deg -90 --sweep-max-deg 90 --sweep-step-deg 2

For 2-node demo: run TWO copies in two terminals, one per node, each
with its own --sdr-serial + --servo-port + --heading-deg.

Exits with non-zero + loud reason on:
* RTL-SDR or servo missing / unopenable.
* Sweep arc < 7 angles (L1 estimator parabola fit requirement).
* Survey out-of-arc: peak heading not in calibrated arc.

Prints a final "LOCKED" line with az ± sigma, parks the servo, and
holds until Ctrl-C. No periodic re-emission; no network traffic.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import time

import numpy as np
from rfmesh_contracts import GeodeticPosition
from rfmesh_dsp.l1 import L1AmplitudeSweepEstimator
from rfmesh_sdr.devices.rtlsdr import RTLSDRDevice
from rfmesh_servo.driver import ServoDriver
from rfmesh_servo.transport import SerialTransport

_LOG = logging.getLogger("bench_lock")

_DEG_FULL_CIRCLE = 360.0
_DEG_HALF_CIRCLE = 180.0
_MIN_OBSERVATIONS = 7
_EMPTY_IQ = np.empty(0, dtype=np.complex64)


def _wrap180(angle_deg: float) -> float:
    return (angle_deg + _DEG_HALF_CIRCLE) % _DEG_FULL_CIRCLE - _DEG_HALF_CIRCLE


def _arc_angles(lo: float, hi: float, step: float) -> list[float]:
    n = round((hi - lo) / step) + 1
    return [lo + i * step for i in range(n)]


async def _sweep_until_peak(
    receiver: RTLSDRDevice,
    servo: ServoDriver,
    estimator: L1AmplitudeSweepEstimator,
    *,
    boresight_deg: float,
    angles: list[float],
    settle_s: float,
    dwell_samples: int,
    stopping: asyncio.Event,
) -> tuple[float, float, float | None]:
    """Sweep until estimator returns a bearing. Returns (az, sigma, snr)."""
    sweep_idx = 0
    while not stopping.is_set():
        sweep_idx += 1
        _LOG.info("sweep %d — parking at %.1f deg", sweep_idx, angles[0])
        await asyncio.to_thread(servo.move, 0, angles[0])
        await asyncio.sleep(settle_s)
        estimator.begin_sweep(time.time_ns())
        for angle in angles:
            if stopping.is_set():
                msg = "interrupted before peak found"
                raise RuntimeError(msg)
            await asyncio.to_thread(servo.move, 0, angle)
            await asyncio.sleep(settle_s)
            iq = await asyncio.to_thread(receiver.read, dwell_samples)
            heading = (boresight_deg + angle) % _DEG_FULL_CIRCLE
            estimator.observe(heading, iq)
        report = estimator.estimate(_EMPTY_IQ)
        if report is not None:
            return float(report.azimuth_deg), float(report.azimuth_sigma_deg), report.snr_db
        _LOG.warning(
            "sweep %d: no peak (%s); retrying",
            sweep_idx,
            estimator.last_refusal_reason,
        )
    msg = "stopped before peak found"
    raise RuntimeError(msg)


async def _main(args: argparse.Namespace) -> int:
    angles = _arc_angles(args.sweep_min_deg, args.sweep_max_deg, args.sweep_step_deg)
    if len(angles) < _MIN_OBSERVATIONS:
        _LOG.error(
            "sweep arc spans only %d angles (need >= %d); widen the arc or shrink step.",
            len(angles), _MIN_OBSERVATIONS,
        )
        return 2

    # Receiver + servo, lazy lifecycle (own with try/finally).
    receiver = RTLSDRDevice(serial=args.sdr_serial)
    servo = ServoDriver(SerialTransport(args.servo_port), own_transport=True)

    # SDRConfig is required by Receiver.configure; build minimal.
    from rfmesh_contracts import (  # noqa: PLC0415
        NodeConfig,
        SDRConfig,
    )

    sdr_cfg = SDRConfig(
        driver="rtlsdr",
        serial=args.sdr_serial,
        sample_rate_hz=float(args.sample_rate_hz),
        center_freq_hz=float(args.center_freq_hz),
        gain_db=float(args.gain_db),
    )
    node_cfg = NodeConfig.model_construct(  # bypass full validation — only used by configure()
        node_id="bench-lock",
        sdr=sdr_cfg,
    )

    estimator = L1AmplitudeSweepEstimator(
        node_id="bench-lock",
        node_position=GeodeticPosition(lat_deg=0.0, lon_deg=0.0, hae_m=0.0, sigma_m=1.0),
        sweep_step_deg=args.sweep_step_deg,
        sweep_dwell_samples=args.dwell_samples,
        peak_prominence_db_min=args.peak_prominence_db_min,
    )

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    receiver.open()
    receiver.configure(node_cfg)
    await asyncio.to_thread(servo.connect)

    try:
        _LOG.info(
            "bench_lock: arc [%.0f, %.0f] step %.1f, boresight %.1f, freq %.3f MHz",
            args.sweep_min_deg, args.sweep_max_deg, args.sweep_step_deg,
            args.heading_deg, args.center_freq_hz / 1e6,
        )
        az, sigma, snr = await _sweep_until_peak(
            receiver, servo, estimator,
            boresight_deg=args.heading_deg,
            angles=angles,
            settle_s=args.settle_s,
            dwell_samples=args.dwell_samples,
            stopping=stopping,
        )
        servo_angle = _wrap180(az - args.heading_deg)
        if not (args.sweep_min_deg <= servo_angle <= args.sweep_max_deg):
            _LOG.error(
                "peak az %.1f deg maps to servo %.1f deg outside arc [%.1f, %.1f] -- "
                "re-survey --heading-deg.",
                az, servo_angle, args.sweep_min_deg, args.sweep_max_deg,
            )
            return 2
        await asyncio.to_thread(servo.move, 0, servo_angle)
        await asyncio.sleep(args.settle_s)
        snr_txt = f"{snr:.1f} dB" if snr is not None else "n/a"
        _LOG.info(
            "LOCKED  az %.1f deg  ± %.1f deg  SNR %s  servo %.1f deg  (Ctrl-C to release)",
            az, sigma, snr_txt, servo_angle,
        )
        await stopping.wait()
    finally:
        with contextlib.suppress(Exception):
            receiver.close()
        with contextlib.suppress(Exception):
            servo.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench_lock",
        description="One-node sweep + peak-find + servo-lock. No backend / no UI.",
    )
    parser.add_argument("--servo-port", required=True, help="ESP32-C6 serial, e.g. /dev/ttyACM0")
    parser.add_argument("--sdr-serial", required=True, help="RTL-SDR V4 serial (rtl_test -t)")
    parser.add_argument("--center-freq-hz", type=float, required=True)
    parser.add_argument("--sample-rate-hz", type=float, default=2.4e6)
    parser.add_argument("--gain-db", type=float, default=30.0)
    parser.add_argument(
        "--heading-deg", type=float, required=True,
        help="surveyed antenna boresight, true degrees CW from north",
    )
    parser.add_argument("--sweep-min-deg", type=float, default=-90.0)
    parser.add_argument("--sweep-max-deg", type=float, default=90.0)
    parser.add_argument("--sweep-step-deg", type=float, default=2.0)
    parser.add_argument("--settle-s", type=float, default=0.20)
    parser.add_argument("--dwell-samples", type=int, default=1024)
    parser.add_argument("--peak-prominence-db-min", type=float, default=6.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
