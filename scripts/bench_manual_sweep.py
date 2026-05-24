"""bench_manual_sweep.py — operator-paced RSSI sweep, no servo.

Skip the servo entirely. Operator rotates the Yagi by hand to each
commanded geographic azimuth (0=N, 90=E, ...), presses Enter, the
script samples IQ from the RTL-SDR + prints the RSSI in dB above the
run's noise floor. After the full arc the script prints a table +
ASCII bar plot + flags the peak, refines it with a 3-point parabolic
fit, computes an honest 1-σ uncertainty from the amplitude-DF CRLB
(B2), and — when ``--fusion-url`` is set — POSTs a ``BearingReport``
to the backend so the soldier UI (``link.html`` pointing arrow,
``locate.html`` fix ellipse) renders the result end-to-end.

Use case: two-node scan-to-UI MVP. Run one terminal per node, each
operator rotates a Yagi. Both peaks POST to the same backend; fusion
batches them into a ``FixEvent`` + 95% confidence ellipse.

Bare CLI (stdout only, no UI push):

    uv run python scripts/bench_manual_sweep.py \\
        --sdr-serial 00000001 \\
        --center-freq-hz 915.0e6 \\
        --start-deg 0 --end-deg 360 --step-deg 30

Full pipeline (POST peak to backend):

    uv run python scripts/bench_manual_sweep.py \\
        --sdr-serial 00000001 --center-freq-hz 915.0e6 \\
        --start-deg 0 --end-deg 360 --step-deg 30 \\
        --fusion-url http://127.0.0.1:8000 \\
        --node-id node-manual-01 \\
        --lat 50.3300 --lon 5.0000

Defaults: 0..360 deg in 30 deg steps (13 samples = full circle).
Adjust ``--step-deg 10`` for finer scan + smaller honest sigma.

Sigma honesty (B2): per-bearing 1-σ uses the amplitude-DF CRLB
approximation ``sigma = HPBW / (1.6 * sqrt(2 * SNR_linear))`` with
``HPBW`` defaulting to 50° (ATK-10 Yagi). Clamped to
``[1°, step_deg]``. Below-floor peaks (prominence < 1 dB) report the
clamp ceiling.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import time
import urllib.error
import urllib.request

import numpy as np
from rfmesh_contracts import NodeConfig, SDRConfig
from rfmesh_contracts.version import SCHEMA_VERSION
from rfmesh_sdr.devices.rtlsdr import RTLSDRDevice

_LOG = logging.getLogger("manual_sweep")
_BAR_WIDTH = 40  # characters
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


def _parabolic_refine(
    angles: list[float], rel_db: list[float], peak_idx: int
) -> tuple[float, float]:
    """3-point parabolic peak interpolation around the discrete maximum.

    Returns ``(refined_angle_deg, refined_prominence_db)``. Falls back
    to the discrete sample when the peak is at an arc endpoint or the
    triple is not concave-down (numerical noise / flat top).
    """
    n = len(angles)
    if peak_idx <= 0 or peak_idx >= n - 1:
        return angles[peak_idx], rel_db[peak_idx]
    y1 = rel_db[peak_idx - 1]
    y2 = rel_db[peak_idx]
    y3 = rel_db[peak_idx + 1]
    denom = y1 - 2.0 * y2 + y3
    if denom >= 0.0:
        return angles[peak_idx], y2
    delta = 0.5 * (y1 - y3) / denom
    step = angles[peak_idx + 1] - angles[peak_idx]
    refined_angle = angles[peak_idx] + delta * step
    refined_db = y2 - 0.25 * (y1 - y3) * delta
    return refined_angle, refined_db


def _honest_sigma_deg(prominence_db: float, step_deg: float, hpbw_deg: float) -> float:
    """1-σ azimuth uncertainty for an amplitude-sweep peak (B2).

    Standard amplitude-DF CRLB approximation
    ``sigma ≈ HPBW / (1.6 * sqrt(2 * SNR_linear))``. SNR_linear is
    derived from the peak's prominence above the run-min floor. The
    result is clamped to ``[1°, step_deg]``: a step-coarse scan cannot
    honestly claim sub-1° accuracy, and a strong-SNR peak is still
    bounded below by the discrete-grid uniform-half-step prior.
    """
    snr_lin = 10.0 ** (max(0.0, prominence_db) / 10.0)
    sigma = hpbw_deg / (1.6 * np.sqrt(2.0 * max(1.0, snr_lin)))
    return float(np.clip(sigma, 1.0, step_deg))


def _print_summary(
    angles: list[float],
    rssis_db: list[float],
    hpbw_deg: float,
    step_deg: float,
) -> tuple[float, float, float]:
    """Print the per-angle bar table + final peak block.

    Returns ``(refined_peak_angle_deg, sigma_deg, prominence_db)`` so
    the caller can hand them to the optional POST step.
    """
    floor = min(rssis_db)
    rel = [r - floor for r in rssis_db]
    peak_idx = int(np.argmax(rel))
    refined_angle, refined_prom = _parabolic_refine(angles, rel, peak_idx)
    sigma = _honest_sigma_deg(refined_prom, step_deg=step_deg, hpbw_deg=hpbw_deg)

    print("\n=== sweep complete ===")
    print(f"floor (min) RSSI: {floor:.1f} dB (this run only, relative)")
    print()
    for a, r, b in zip(angles, rel, [_bar(r) for r in rel], strict=True):
        marker = "  <-- PEAK" if a == angles[peak_idx] else ""
        print(f"  {a:>6.1f} deg | RSSI {r:+6.2f} dB | {b}{marker}")
    print()
    print(
        f"PEAK heading (refined): {refined_angle % 360.0:6.2f} deg "
        f"± {sigma:.2f} deg (1-σ)  "
        f"[prominence {refined_prom:+5.2f} dB above floor]"
    )
    # Top-3 too, in case the peak is close to a sidelobe.
    order = np.argsort(rel)[::-1][:3]
    print("\nTop 3:")
    for rank, idx in enumerate(order, start=1):
        print(f"  #{rank}: {angles[idx]:>6.1f} deg  {rel[idx]:+6.2f} dB")
    return refined_angle % 360.0, sigma, refined_prom


def _build_bearing_payload(
    *,
    node_id: str,
    lat_deg: float,
    lon_deg: float,
    hae_m: float,
    node_sigma_m: float,
    azimuth_deg: float,
    azimuth_sigma_deg: float,
    snr_db: float,
) -> dict[str, object]:
    """Build a ``BearingReport`` JSON dict the backend ``/bearings`` accepts.

    ``method = "l1_rssi"`` (Capability enum value), ``prior_kind =
    "flat"`` (unknown emitter — fusion uses for FixEvent computation
    per ADR-026). ``snr_db`` is dB above this run's noise-floor
    estimate (B.2: never absolute dBm).
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "node_id": node_id,
        "t_unix_ns": time.time_ns(),
        "node_position": {
            "lat_deg": lat_deg,
            "lon_deg": lon_deg,
            "hae_m": hae_m,
            "sigma_m": node_sigma_m,
        },
        "azimuth_deg": azimuth_deg % 360.0,
        "azimuth_sigma_deg": azimuth_sigma_deg,
        "method": "l1_rssi",
        "snr_db": snr_db,
        "prior_kind": "flat",
    }


def _post_bearing(fusion_url: str, payload: dict[str, object]) -> tuple[int, str]:
    """POST one BearingReport to ``{fusion_url}/bearings``. Returns (status, body)."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{fusion_url.rstrip('/')}/bearings",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


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
        description="Operator-paced RSSI sweep. No servo. Two-node scan-to-UI MVP.",
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
    parser.add_argument(
        "--antenna-hpbw-deg",
        type=float,
        default=50.0,
        help="antenna half-power beamwidth in azimuth (default 50° for ATK-10 Yagi)",
    )
    # Optional backend POST. When --fusion-url is supplied, --node-id +
    # --lat + --lon become REQUIRED (validated below) so the bearing
    # carries an honest position (fusion weight depends on it, B2).
    parser.add_argument(
        "--fusion-url",
        type=str,
        default=None,
        help="Backend base URL (e.g. http://127.0.0.1:8000). Sends peak to /bearings.",
    )
    parser.add_argument(
        "--node-id",
        type=str,
        default=None,
        help="node_id on the wire (required with --fusion-url; defaults to --node-label)",
    )
    parser.add_argument(
        "--lat", type=float, default=None, help="node latitude (required with --fusion-url)"
    )
    parser.add_argument(
        "--lon", type=float, default=None, help="node longitude (required with --fusion-url)"
    )
    parser.add_argument(
        "--hae-m",
        type=float,
        default=0.0,
        help="node height above WGS-84 ellipsoid in metres (default 0)",
    )
    parser.add_argument(
        "--node-sigma-m",
        type=float,
        default=5.0,
        help="1-σ position uncertainty in metres (default 5; honest smartphone-GPS value)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(message)s")

    if args.fusion_url is not None and (args.lat is None or args.lon is None):
        print(
            "error: --fusion-url requires --lat and --lon "
            "(node position is fusion's weight input — B2 honesty)",
            file=sys.stderr,
        )
        return 2

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

    peak_angle, sigma_deg, prominence_db = _print_summary(
        visited, rssis_db, hpbw_deg=args.antenna_hpbw_deg, step_deg=args.step_deg
    )

    if args.fusion_url is None:
        print("\n(no --fusion-url set; not POSTing to backend)")
        return 0

    node_id = args.node_id or args.node_label
    payload = _build_bearing_payload(
        node_id=node_id,
        lat_deg=float(args.lat),
        lon_deg=float(args.lon),
        hae_m=float(args.hae_m),
        node_sigma_m=float(args.node_sigma_m),
        azimuth_deg=peak_angle,
        azimuth_sigma_deg=sigma_deg,
        snr_db=prominence_db,
    )
    print(f"\nPOST {args.fusion_url.rstrip('/')}/bearings  (node_id={node_id})")
    try:
        status, body = _post_bearing(args.fusion_url, payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        print(f"  HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"  network error: {exc.reason}", file=sys.stderr)
        return 1
    print(f"  HTTP {status}: {body}")
    print(
        "\nSoldier UI:\n"
        f"  link.html   ->  {args.fusion_url.rstrip('/')}/link.html   "
        "(pointing arrow + sweep panel)\n"
        f"  locate.html ->  {args.fusion_url.rstrip('/')}/locate.html "
        "(LOB ray; ellipse appears once 2nd node POSTs)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
