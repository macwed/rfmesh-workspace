#!/usr/bin/env python3
"""First-light L1 smoke test: prove the servo + RTL-SDR + estimator chain.

Runs ONE amplitude-comparison sweep and prints the bearing -- the smallest
end-to-end check that the field hardware path works, before any Node /
backend wiring. Use it on the bench the moment the gear is plugged in.

Modes
-----
  --sim     No hardware. A synthetic beam (Gaussian RSSI bump at a chosen
            true azimuth) drives the real ``L1AmplitudeSweepEstimator`` so
            you can verify the sweep logic + output format anywhere
            (CI / a laptop with no SDR). Prints the recovered bearing and
            the error vs the injected truth.

  (default) Hardware. Opens the RTL-SDR (``RTLSDRDevice``) and the ESP32
            servo (``ServoDriver``), sweeps the arc, prints the bearing.

Examples
--------
  python scripts/firstlight_l1.py --sim --true-az 12
  python scripts/firstlight_l1.py --servo-port /dev/ttyACM0 --freq 915e6 \\
      --rate 2.4e6 --gain 30 --boresight 90

This script is intentionally standalone (no asyncio, no Node) so a failure
points straight at one link in the chain.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
from rfmesh_contracts import GeodeticPosition
from rfmesh_dsp.l1 import L1AmplitudeSweepEstimator

_DEG_FULL_CIRCLE = 360.0


def _angles(min_deg: float, max_deg: float, step_deg: float) -> list[float]:
    n = round((max_deg - min_deg) / step_deg) + 1
    return [min_deg + i * step_deg for i in range(n)]


def _synthetic_iq(
    servo_angle_deg: float,
    true_offset_deg: float,
    n: int,
    *,
    hpbw_deg: float = 50.0,
    snr_db: float = 30.0,
    rng: np.random.Generator,
) -> np.ndarray:
    """Synthetic IQ whose power follows a Gaussian beam centred on the emitter.

    ``true_offset_deg`` is the emitter's offset from boresight; the gain at
    ``servo_angle_deg`` falls off as a Gaussian of the angular miss, scaled
    so the on-peak SNR is ``snr_db`` above a unit noise floor.
    """
    sigma = hpbw_deg / 2.3548  # FWHM -> Gaussian sigma
    miss = servo_angle_deg - true_offset_deg
    gain = float(np.exp(-0.5 * (miss / sigma) ** 2))
    signal_amp = np.sqrt(10.0 ** (snr_db / 10.0)) * gain
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2.0)
    tone = signal_amp * np.exp(1j * 2.0 * np.pi * 0.01 * np.arange(n))
    return (tone + noise).astype(np.complex64)


def _run_sim(args: argparse.Namespace) -> int:
    rng = np.random.default_rng(args.seed)
    est = L1AmplitudeSweepEstimator(
        node_id="firstlight-sim",
        node_position=GeodeticPosition(lat_deg=50.33, lon_deg=5.0, hae_m=200.0, sigma_m=5.0),
        sweep_step_deg=args.sweep_step_deg,
        sweep_dwell_samples=args.dwell_samples,
    )
    est.begin_sweep(time.time_ns())
    for angle in _angles(args.sweep_min_deg, args.sweep_max_deg, args.sweep_step_deg):
        iq = _synthetic_iq(angle, args.true_az, args.dwell_samples, rng=rng)
        heading = (args.boresight + angle) % _DEG_FULL_CIRCLE
        est.observe(heading, iq)
    report = est.estimate(np.empty(0, dtype=np.complex64))
    if report is None:
        print(f"FAIL: no bearing ({est.last_refusal_reason})")
        return 1
    truth = (args.boresight + args.true_az) % _DEG_FULL_CIRCLE
    err = (report.azimuth_deg - truth + 180.0) % _DEG_FULL_CIRCLE - 180.0
    print(
        f"OK  bearing {report.azimuth_deg:6.2f} +/- {report.azimuth_sigma_deg:.2f} deg  "
        f"(truth {truth:.2f}, err {err:+.2f} deg, SNR {report.snr_db:.1f} dB)"
    )
    return 0


def _run_hardware(args: argparse.Namespace) -> int:
    from rfmesh_contracts import NodeConfig  # noqa: PLC0415
    from rfmesh_sdr.devices.rtlsdr import RTLSDRDevice  # noqa: PLC0415
    from rfmesh_servo.driver import ServoDriver  # noqa: PLC0415
    from rfmesh_servo.transport import SerialTransport  # noqa: PLC0415

    if args.servo_port is None:
        print("ERROR: --servo-port is required in hardware mode (or pass --sim).")
        return 2

    # Minimal NodeConfig just to drive RTLSDRDevice.configure() tuning.
    cfg = NodeConfig.model_validate(
        {
            "node_id": "firstlight",
            "position": {"lat_deg": 50.33, "lon_deg": 5.0, "hae_m": 200.0, "sigma_m": 5.0},
            "heading_deg": args.boresight,
            "sdr": {
                "driver": "rtlsdr",
                "sample_rate_hz": args.rate,
                "center_freq_hz": args.freq,
                "gain_db": args.gain,
            },
            "capabilities": ["l1_rssi"],
            "bearer": {"kind": "wifi", "heartbeat_interval_s": 2.0},
            "fusion_endpoint": "udp://127.0.0.1:9000",
        }
    )

    receiver = RTLSDRDevice(serial=args.rtl_serial)
    servo = ServoDriver(SerialTransport(args.servo_port), own_transport=True)
    est = L1AmplitudeSweepEstimator(
        node_id="firstlight",
        node_position=cfg.position,
        sweep_step_deg=args.sweep_step_deg,
        sweep_dwell_samples=args.dwell_samples,
    )

    print("first-light: opening RTL-SDR + servo ...")
    receiver.open()
    receiver.configure(cfg)
    servo.connect()
    try:
        angles = _angles(args.sweep_min_deg, args.sweep_max_deg, args.sweep_step_deg)
        servo.move(0, angles[0])  # park low; same-side approach
        time.sleep(args.settle_ms / 1000.0)
        est.begin_sweep(time.time_ns())
        for angle in angles:
            servo.move(0, angle)
            time.sleep(args.settle_ms / 1000.0)
            iq = receiver.read(args.dwell_samples)
            heading = (args.boresight + angle) % _DEG_FULL_CIRCLE
            est.observe(heading, iq)
            print(f"  angle {angle:+6.1f} -> heading {heading:6.1f} deg", end="\r")
        report = est.estimate(np.empty(0, dtype=np.complex64))
    finally:
        receiver.close()
        servo.close()

    print()
    if report is None:
        print(f"FAIL: no bearing ({est.last_refusal_reason})")
        return 1
    snr = report.snr_db if report.snr_db is not None else float("nan")
    print(
        f"OK  bearing {report.azimuth_deg:6.2f} +/- {report.azimuth_sigma_deg:.2f} deg  "
        f"(prominence/SNR {snr:.1f} dB)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="First-light L1 sweep smoke test.")
    p.add_argument("--sim", action="store_true", help="run synthetic (no hardware)")
    p.add_argument("--servo-port", default=None, help="ESP32 serial port (/dev/ttyACM0, COM5)")
    p.add_argument("--rtl-serial", default=None, help="RTL-SDR serial (None = first device)")
    p.add_argument("--freq", type=float, default=915e6, help="centre frequency Hz (default 915e6)")
    p.add_argument("--rate", type=float, default=2.4e6, help="sample rate Hz (default 2.4e6)")
    p.add_argument("--gain", type=float, default=30.0, help="gain dB (default 30)")
    p.add_argument("--boresight", type=float, default=90.0, help="antenna boresight azimuth deg")
    p.add_argument("--true-az", type=float, default=10.0, help="[sim] emitter offset, deg")
    p.add_argument("--sweep-min-deg", type=float, default=-90.0)
    p.add_argument("--sweep-max-deg", type=float, default=90.0)
    p.add_argument("--sweep-step-deg", type=float, default=2.0)
    p.add_argument("--settle-ms", type=float, default=200.0)
    p.add_argument("--dwell-samples", type=int, default=1024)
    p.add_argument("--seed", type=int, default=0, help="[sim] RNG seed")
    args = p.parse_args(argv)
    return _run_sim(args) if args.sim else _run_hardware(args)


if __name__ == "__main__":
    sys.exit(main())
