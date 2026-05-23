#!/usr/bin/env python3
"""Servo bring-up: connect, load a coarse calibration, prove motion.

The fastest path from "ESP32 flashed + servo wired" to "the antenna moves
on command". It connects to the controller, optionally writes the default
MG996R calibration and persists it to NVS, then sweeps the axis so you can
watch the Yagi swing.

  python scripts/servo_bringup.py --port /dev/ttyACM0            # connect + sweep
  python scripts/servo_bringup.py --port /dev/ttyACM0 --set-default-cal

Coarse default cal (500-2500 us, +/-90 deg) is "moves now", not "points
true". Precise per-axis calibration (MG996R clones vary, INHERITED_CONTEXT
.md §1.2) is a later step; this just confirms the link and the mechanics.

If the controller does not answer, the firmware may not be flashed -- flash
it from a host with esp-idf:  cd firmware && idf.py -p <port> flash
"""

from __future__ import annotations

import argparse
import sys
import time

from rfmesh_servo.driver import ServoDriver
from rfmesh_servo.messages import Calibration
from rfmesh_servo.transport import SerialTransport


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ESP32 servo bring-up + motion test.")
    p.add_argument("--port", required=True, help="serial port (/dev/ttyACM0 or COM5)")
    p.add_argument("--axis", type=int, default=0, help="servo axis (default 0)")
    p.add_argument(
        "--set-default-cal",
        action="store_true",
        help="write + persist the default MG996R calibration before sweeping",
    )
    p.add_argument("--sweep-deg", type=float, default=45.0, help="sweep amplitude (default 45)")
    p.add_argument("--settle-s", type=float, default=0.6, help="pause at each angle (default 0.6)")
    args = p.parse_args(argv)

    with ServoDriver(SerialTransport(args.port), own_transport=True) as servo:
        pong = servo.connect()
        print(f"connected: proto v{pong.proto_version}, {pong.axis_count} axis/axes")

        if args.set_default_cal:
            cal = Calibration.default(args.axis)
            servo.set_calibration(cal)
            servo.persist_calibration(args.axis)
            print(
                f"calibration set+persisted: axis {args.axis} "
                f"[{cal.pulse_min_us}-{cal.pulse_max_us} us, "
                f"{cal.angle_min_deg:+.0f}..{cal.angle_max_deg:+.0f} deg]"
            )

        try:
            current = servo.get_calibration(args.axis)
            print(
                f"axis {args.axis} calibration: "
                f"[{current.pulse_min_us}-{current.pulse_max_us} us, "
                f"{current.angle_min_deg:+.0f}..{current.angle_max_deg:+.0f} deg]"
            )
        except Exception as exc:
            print(f"WARN: could not read calibration ({exc}); pass --set-default-cal first")

        print("sweeping: 0 -> -A -> +A -> 0 (watch the antenna move)")
        for angle in (0.0, -args.sweep_deg, args.sweep_deg, 0.0):
            servo.move(args.axis, angle)
            time.sleep(args.settle_s)
            pos = servo.position(args.axis)
            print(f"  commanded {angle:+6.1f} deg -> reported {pos.commanded_angle_deg:+6.1f} deg")

    print("OK servo bring-up complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
