"""Guided servo calibration over servo UART v1 (``rfmesh-servo-calibrate``).

Walks the operator through aligning a servo's mechanical sweep to its PWM
range and persists the resulting :class:`Calibration` to NVS. Implements
the procedure sketched in §3.7 of ``docs/wire-protocols/servo_uart_v1.md``:

1. Push a bootstrap calibration so a known angle maps to ``pulse_min_us``.
2. Drive the axis there; ask the operator for the observed mechanical
   angle.
3. Repeat for ``pulse_max_us``.
4. Build the real :class:`Calibration` from the operator's two readings,
   write it (RAM), and ask before persisting.

The interactive parts take injectable ``input_fn``/``print_fn`` callables
so the procedure is unit-testable without a TTY or real driver.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Final

from rfmesh_servo.driver import ServoDriver
from rfmesh_servo.exceptions import ServoCommandError, ServoProtocolError
from rfmesh_servo.messages import Calibration
from rfmesh_servo.transport import SerialTransport

_PULSE_MIN_BOUND_US: Final[int] = 400
_PULSE_MAX_BOUND_US: Final[int] = 2600


def compute_calibration(
    axis: int,
    pulse_min_us: int,
    pulse_max_us: int,
    angle_at_pulse_min_deg: float,
    angle_at_pulse_max_deg: float,
) -> Calibration:
    """Build and validate a :class:`Calibration` from operator-observed endpoints.

    The firmware applies ``pulse_us = pulse_min + (angle - angle_min) *
    (pulse_max - pulse_min) / (angle_max - angle_min)`` (servo_uart_v1
    §3.2). This helper just packages the four endpoints into the
    dataclass and runs the same validation the firmware would apply on
    ``CAL_SET``.

    Raises:
        ValueError: Pulse out of ``[400, 2600]``, pulses inverted, or
            angles inverted (matches firmware ``ERR_BAD_CALIBRATION``).
    """
    cal = Calibration(
        axis=axis,
        pulse_min_us=pulse_min_us,
        pulse_max_us=pulse_max_us,
        angle_min_deg=angle_at_pulse_min_deg,
        angle_max_deg=angle_at_pulse_max_deg,
    )
    cal.validate()
    return cal


def _ask_float(
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
    prompt: str,
) -> float:
    while True:
        raw = input_fn(prompt).strip()
        try:
            return float(raw)
        except ValueError:
            print_fn(f"not a number: {raw!r}")


def _ask_yes_no(input_fn: Callable[[str], str], prompt: str) -> bool:
    answer = input_fn(prompt).strip().lower()
    return answer in {"y", "yes"}


def run_calibration(
    driver: ServoDriver,
    axis: int,
    *,
    pulse_min_us: int = Calibration.DEFAULT_PULSE_MIN_US,
    pulse_max_us: int = Calibration.DEFAULT_PULSE_MAX_US,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> Calibration:
    """Drive the §3.7 calibration procedure on a connected :class:`ServoDriver`.

    The host has no "set raw pulse" command, so the procedure first writes
    a bootstrap calibration whose endpoints map ``angle_min_deg=-90 ↔
    pulse_min_us`` and ``angle_max_deg=+90 ↔ pulse_max_us``. Issuing
    ``MOVE`` to ``-90`` then ``+90`` exposes the two pulse endpoints to
    the operator's eye, who reports the actual mechanical angles
    observed. Those readings replace the bootstrap angles to give the
    real calibration.

    Args:
        driver: A connected :class:`ServoDriver`.
        axis: Axis ID to calibrate.
        pulse_min_us: Pulse width to use for the lower endpoint
            (default 500 µs from §3.7).
        pulse_max_us: Pulse width for the upper endpoint (default 2500 µs).
        input_fn: Callable used to prompt the operator. Defaults to
            built-in :func:`input`.
        print_fn: Callable used for user-facing output. Defaults to
            :func:`print`.

    Returns:
        The :class:`Calibration` written to firmware RAM (and possibly
        persisted, if the operator confirmed).

    Raises:
        ValueError: ``pulse_min_us``/``pulse_max_us`` outside [400, 2600]
            or inverted.
    """
    if not _PULSE_MIN_BOUND_US <= pulse_min_us <= _PULSE_MAX_BOUND_US:
        raise ValueError(
            f"pulse_min_us {pulse_min_us} outside [{_PULSE_MIN_BOUND_US}, {_PULSE_MAX_BOUND_US}]"
        )
    if not _PULSE_MIN_BOUND_US <= pulse_max_us <= _PULSE_MAX_BOUND_US:
        raise ValueError(
            f"pulse_max_us {pulse_max_us} outside [{_PULSE_MIN_BOUND_US}, {_PULSE_MAX_BOUND_US}]"
        )
    if pulse_min_us >= pulse_max_us:
        raise ValueError(f"pulse_min_us ({pulse_min_us}) must be < pulse_max_us ({pulse_max_us})")

    print_fn(f"servo_calibrate v1 — axis {axis}")
    print_fn("")
    print_fn("This procedure determines the relationship between PWM pulse width and")
    print_fn("mechanical angle for your servo. You will be asked to manually align the")
    print_fn("mast to known reference angles.")
    print_fn("")

    bootstrap = Calibration(
        axis=axis,
        pulse_min_us=pulse_min_us,
        pulse_max_us=pulse_max_us,
        angle_min_deg=Calibration.DEFAULT_ANGLE_MIN_DEG,
        angle_max_deg=Calibration.DEFAULT_ANGLE_MAX_DEG,
    )
    driver.set_calibration(bootstrap)

    print_fn(f"Step 1: I will set the servo to pulse_min ({pulse_min_us} µs). Observe the mast and")
    print_fn("manually note its angle relative to your reference (e.g. north, or some")
    print_fn("fixed mark on the mount). When ready, press ENTER.")
    input_fn("[ENTER] ")
    driver.move(axis, Calibration.DEFAULT_ANGLE_MIN_DEG)

    angle_at_min = _ask_float(
        input_fn,
        print_fn,
        "Now, what mechanical angle is the mast pointing at? (degrees, signed) > ",
    )

    print_fn("")
    print_fn(f"Step 2: I will set the servo to pulse_max ({pulse_max_us} µs). Same as before.")
    input_fn("[ENTER] ")
    driver.move(axis, Calibration.DEFAULT_ANGLE_MAX_DEG)

    angle_at_max = _ask_float(input_fn, print_fn, "Mechanical angle? > ")
    while True:
        try:
            cal = compute_calibration(axis, pulse_min_us, pulse_max_us, angle_at_min, angle_at_max)
        except ValueError as exc:
            # Either endpoint can be the typo (the second prompt is under more
            # time pressure), so re-ask both — clearer than guessing which
            # value the operator wants to amend.
            print_fn(f"rejected: {exc}")
            print_fn("Re-enter both endpoint angles.")
            angle_at_min = _ask_float(
                input_fn,
                print_fn,
                "Re-enter angle at pulse_min (degrees, signed) > ",
            )
            angle_at_max = _ask_float(
                input_fn,
                print_fn,
                "Re-enter angle at pulse_max (degrees, signed) > ",
            )
            continue
        break

    print_fn("")
    print_fn("Calibration computed:")
    print_fn(f"  pulse_min_us={cal.pulse_min_us}, pulse_max_us={cal.pulse_max_us}")
    print_fn(f"  angle_min_deg={cal.angle_min_deg}, angle_max_deg={cal.angle_max_deg}")
    print_fn("")

    driver.set_calibration(cal)

    if _ask_yes_no(input_fn, "Persist to NVS? [y/N] "):
        driver.persist_calibration(axis)
        print_fn("Persisted.")
    else:
        print_fn("Not persisted (RAM only).")
    print_fn("Done.")
    return cal


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rfmesh-servo-calibrate",
        description=(
            "Guided servo calibration over servo UART v1 "
            "(see docs/wire-protocols/servo_uart_v1.md §3.6, §3.7)."
        ),
    )
    parser.add_argument(
        "--port",
        required=True,
        help="Serial device path, e.g. /dev/ttyACM0",
    )
    parser.add_argument(
        "--axis",
        type=int,
        default=0,
        help="Axis to calibrate (default 0)",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=115200,
        help="Nominal baud (USB-CDC ignores; default 115200)",
    )
    parser.add_argument(
        "--pulse-min",
        type=int,
        default=Calibration.DEFAULT_PULSE_MIN_US,
        help="Lower-endpoint pulse width in µs (default 500)",
    )
    parser.add_argument(
        "--pulse-max",
        type=int,
        default=Calibration.DEFAULT_PULSE_MAX_US,
        help="Upper-endpoint pulse width in µs (default 2500)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not _PULSE_MIN_BOUND_US <= args.pulse_min <= _PULSE_MAX_BOUND_US:
        print(
            f"--pulse-min {args.pulse_min} outside [{_PULSE_MIN_BOUND_US}, {_PULSE_MAX_BOUND_US}]",
            file=sys.stderr,
        )
        return 2
    if not _PULSE_MIN_BOUND_US <= args.pulse_max <= _PULSE_MAX_BOUND_US:
        print(
            f"--pulse-max {args.pulse_max} outside [{_PULSE_MIN_BOUND_US}, {_PULSE_MAX_BOUND_US}]",
            file=sys.stderr,
        )
        return 2
    if args.pulse_min >= args.pulse_max:
        print("--pulse-min must be < --pulse-max", file=sys.stderr)
        return 2

    try:
        transport = SerialTransport(args.port, baudrate=args.baudrate)
    except (ImportError, OSError) as exc:
        print(f"failed to open {args.port}: {exc}", file=sys.stderr)
        return 2

    with ServoDriver(transport, own_transport=True) as driver:
        try:
            driver.connect()
        except ServoProtocolError as exc:
            print(f"connect failed: {exc}", file=sys.stderr)
            return 1
        try:
            run_calibration(
                driver,
                args.axis,
                pulse_min_us=args.pulse_min,
                pulse_max_us=args.pulse_max,
            )
        except (KeyboardInterrupt, EOFError):
            print()
            print("aborted by user", file=sys.stderr)
            return 130
        except ServoCommandError as exc:
            print(f"firmware error: {exc}", file=sys.stderr)
            return 1
        except ServoProtocolError as exc:
            print(f"protocol error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
