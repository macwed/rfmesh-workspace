"""bench_manual_sweep.py — operator-paced RSSI sweep, no servo.

Skip the servo entirely. Operator rotates the Yagi by hand to each
commanded angle, presses Enter, the script samples IQ from the
RTL-SDR + prints the RSSI in dB above the run's noise floor. After
the full arc the script prints a table + ASCII bar plot + flags the
peak.

Use case: two-node smoke test. Run one terminal per node, operator
on each Yagi follows the prompts. The peak heading printed at the
end is each node's bearing to the emitter. Operator eyeballs two
peak headings, sanity-checks they converge on a common point.

CLI:

    uv run python scripts/bench_manual_sweep.py \\
        --sdr-serial 00000001 \\
        --center-freq-hz 915.0e6 \\
        --start-deg 0 --end-deg 360 --step-deg 30

Defaults: 0..360 deg in 30 deg steps (13 samples = full circle).
Adjust --step-deg 10 for finer scan.

Output (per step + final summary):

      0 deg | RSSI  +0.0 dB | ###
     30 deg | RSSI  +2.3 dB | #######
     60 deg | RSSI +14.7 dB | ##################################  <-- PEAK
     ...

    Peak heading: 60 deg  (RSSI +14.7 dB above floor)
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys

import numpy as np
from rfmesh_contracts import NodeConfig, SDRConfig
from rfmesh_sdr.devices.rtlsdr import RTLSDRDevice

_LOG = logging.getLogger("manual_sweep")
_BAR_WIDTH = 40   # characters
_BAR_MAX_DB = 40  # dB above floor for full bar


def _rssi_db(iq: np.ndarray) -> float:
    """Mean power of an IQ block, in dB (relative — caller subtracts floor)."""
    power = float(np.mean(np.abs(iq) ** 2))
    if power <= 0.0:
        return -120.0  # arbitrary floor; never silently NaN
    return 10.0 * np.log10(power)


def _arc_angles(start: float, end: float, step: float) -> list[float]:
    if step <= 0:
        msg = f"--step-deg must be > 0 (got {step})"
        raise ValueError(msg)
    if end <= start:
        msg = f"--end-deg ({end}) must be > --start-deg ({start})"
        raise ValueError(msg)
    n = round((end - start) / step) + 1
    return [start + i * step for i in range(n)]


def _bar(db_rel: float) -> str:
    span = min(max(db_rel, 0.0), _BAR_MAX_DB)
    n = round(span * _BAR_WIDTH / _BAR_MAX_DB)
    return "#" * n


def _print_summary(angles: list[float], rssis_db: list[float]) -> None:
    floor = min(rssis_db)
    rel = [r - floor for r in rssis_db]
    peak_idx = int(np.argmax(rel))
    peak_angle = angles[peak_idx]
    peak_db = rel[peak_idx]

    print("\n=== sweep complete ===")
    print(f"floor (min) RSSI: {floor:.1f} dB (this run only, relative)")
    print()
    for a, r, b in zip(angles, rel, [_bar(r) for r in rel], strict=True):
        marker = "  <-- PEAK" if r == peak_db else ""
        print(f"  {a:>6.1f} deg | RSSI {r:+6.2f} dB | {b}{marker}")
    print()
    print(f"PEAK heading: {peak_angle:.1f} deg  ({peak_db:+.2f} dB above floor)")
    # Top-3 too, in case the peak is close to a sidelobe.
    order = np.argsort(rel)[::-1][:3]
    print("\nTop 3:")
    for rank, idx in enumerate(order, start=1):
        print(f"  #{rank}: {angles[idx]:>6.1f} deg  {rel[idx]:+6.2f} dB")


def _prompt(prompt: str) -> bool:
    """Returns True to continue, False on Ctrl-D / 'q'."""
    try:
        line = input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return line.strip().lower() not in {"q", "quit", "exit"}


def _drain(receiver: RTLSDRDevice, n_blocks: int, samples_per: int) -> np.ndarray:
    """Concatenate ``n_blocks`` reads so a single bad block can't skew RSSI."""
    chunks: list[np.ndarray] = []
    for _ in range(n_blocks):
        chunks.append(receiver.read(samples_per))
    return np.concatenate(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench_manual_sweep",
        description="Operator-paced RSSI sweep. No servo. Two-node smoke test.",
    )
    parser.add_argument("--sdr-serial", required=True, help="RTL-SDR serial (rtl_test -t)")
    parser.add_argument("--center-freq-hz", type=float, required=True)
    parser.add_argument("--sample-rate-hz", type=float, default=2.4e6)
    parser.add_argument("--gain-db", type=float, default=30.0)
    parser.add_argument("--start-deg", type=float, default=0.0)
    parser.add_argument("--end-deg", type=float, default=360.0)
    parser.add_argument("--step-deg", type=float, default=30.0)
    parser.add_argument("--dwell-blocks", type=int, default=4, help="IQ blocks per heading")
    parser.add_argument("--dwell-samples", type=int, default=8192, help="samples per block")
    parser.add_argument("--node-label", type=str, default="NODE", help="prefix for prompts")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(message)s")

    try:
        angles = _arc_angles(args.start_deg, args.end_deg, args.step_deg)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"[{args.node_label}] sweep {args.start_deg:.0f}..{args.end_deg:.0f} deg "
        f"step {args.step_deg:.0f} deg ({len(angles)} samples)"
    )

    receiver = RTLSDRDevice(serial=args.sdr_serial)
    sdr_cfg = SDRConfig(
        driver="rtlsdr",
        serial=args.sdr_serial,
        sample_rate_hz=float(args.sample_rate_hz),
        center_freq_hz=float(args.center_freq_hz),
        gain_db=float(args.gain_db),
    )
    node_cfg = NodeConfig.model_construct(node_id="manual-sweep", sdr=sdr_cfg)

    try:
        receiver.open()
        receiver.configure(node_cfg)
    except Exception as exc:
        print(f"[{args.node_label}] receiver init failed: {exc}", file=sys.stderr)
        return 2

    rssis_db: list[float] = []
    visited: list[float] = []
    try:
        for angle in angles:
            if not _prompt(
                f"\n[{args.node_label}] point Yagi at {angle:6.1f} deg, "
                "hold steady, press Enter (q=quit): "
            ):
                break
            try:
                iq = _drain(receiver, args.dwell_blocks, args.dwell_samples)
            except Exception as exc:
                print(f"  read failed at {angle:.1f} deg: {exc}", file=sys.stderr)
                continue
            db = _rssi_db(iq)
            rssis_db.append(db)
            visited.append(angle)
            # Live feedback: relative to running min.
            running_floor = min(rssis_db)
            rel = db - running_floor
            print(
                f"  {angle:6.1f} deg | absolute {db:+7.2f} dB | "
                f"rel-to-floor {rel:+5.2f} dB | {_bar(rel)}"
            )
    finally:
        with contextlib.suppress(Exception):
            receiver.close()

    if not rssis_db:
        print(f"[{args.node_label}] no samples collected", file=sys.stderr)
        return 2

    _print_summary(visited, rssis_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
