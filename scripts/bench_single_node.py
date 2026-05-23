"""bench_single_node.py — real-bench single-node sweep → bearing → WebSocket.

One command, end-to-end:

    uv run python scripts/bench_single_node.py \
        --servo-port /dev/ttyACM0 \
        --center-freq-hz 958695000 \
        --sample-rate-hz 2048000 \
        --gain-db 40.0 \
        --sweep-min-deg -60 --sweep-max-deg 60 --sweep-step-deg 5

Wires the production rfmesh stack against real hardware (no simulator):

    ServoDriver  (C6 firmware on /dev/ttyACM*) -+
                                                |  L1AmplitudeSweepEstimator
    RTLSDRDevice (rtl_sdr subprocess)          -+        |
                                                         v
                                                    BearingReport
                                                         |
                                                         v
                                                  DashboardPubSub
                                                  /            \\
                                  WebSocket endpoint        stdout
                                  (ws://host:port/path)     (--print-bearings)

For single-node operation no fusion happens (need ≥2 bearings for a fix);
the dashboard panels that consume ``publish_bearing`` (BearingsPanel,
NodeStatusPanel) update from live reports. Connect ``rfmesh-ops`` in a
second terminal:

    uv run rfmesh-ops --connect ws://127.0.0.1:9001/dashboard --layout debug

Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import signal
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import numpy as np
from aiohttp import web
from rfmesh_contracts import (
    BearerConfig,
    BearerKind,
    Capability,
    GeodeticPosition,
    NodeConfig,
    SDRConfig,
)
from rfmesh_dsp.l1 import L1AmplitudeSweepEstimator
from rfmesh_node.dashboard_pubsub import DashboardPubSub, WebSocketSubscriber
from rfmesh_sdr.devices import RTLSDRDevice
from rfmesh_servo.driver import ServoDriver
from rfmesh_servo.messages import NEVER_MOVED_SENTINEL
from rfmesh_servo.transport import SerialTransport

_LOG = logging.getLogger("bench_single_node")
_NEVER_MOVED_SENTINEL = NEVER_MOVED_SENTINEL
# Threshold below which a smooth-move call is treated as a no-op (the move is
# smaller than what the servo can mechanically distinguish from its current
# position anyway, and would otherwise divide-by-zero in the step loop).
_MOTION_NOOP_DEG: float = 1e-6


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bench_single_node",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Servo
    p.add_argument("--servo-port", default="/dev/ttyACM0", help="Servo USB-CDC port.")
    p.add_argument("--axis", type=int, default=0, help="Servo axis to drive (default 0).")
    p.add_argument(
        "--servo-settle-s",
        type=float,
        default=0.08,
        help="Extra dwell after the smooth-move final step, before reading IQ (default 0.08 s).",
    )
    p.add_argument(
        "--servo-max-vel-dps",
        type=float,
        default=25.0,
        help=(
            "Cruise angular velocity of the trapezoidal motion profile, "
            "degrees per second (default 25). Lower = gentler on gears + mast."
        ),
    )
    p.add_argument(
        "--servo-accel-time-s",
        type=float,
        default=0.40,
        help=(
            "Ramp-up and ramp-down duration each side of cruise, seconds "
            "(default 0.40). Longer = smoother start/stop, more momentum control."
        ),
    )
    p.add_argument(
        "--servo-motion-step-deg",
        type=float,
        default=1.0,
        help=(
            "Discretisation step of the motion profile, degrees (default 1.0). "
            "Smaller = smoother motion, more USB-CDC traffic."
        ),
    )
    p.add_argument(
        "--servo-fast-threshold-deg",
        type=float,
        default=10.0,
        help=(
            "Moves with |delta| <= this many degrees use a single fast MOVE "
            "+ fixed settle (no ramp), bypassing the trapezoidal profile. "
            "Small moves carry little momentum and don't need ramping; ramping "
            "them makes per-step sweeps drag (default 10.0)."
        ),
    )
    p.add_argument(
        "--servo-fast-settle-s",
        type=float,
        default=0.10,
        help=(
            "Dwell after a fast (small-move) MOVE before returning, seconds "
            "(default 0.10). Just enough for the servo to physically reach "
            "the new position."
        ),
    )
    p.add_argument(
        "--servo-park-deg",
        type=float,
        default=None,
        help=(
            "If set, smoothly move the servo to this angle on shutdown (Ctrl-C). "
            "Useful to leave the mast in a safe orientation."
        ),
    )
    p.add_argument(
        "--invert-direction",
        action="store_true",
        help=(
            "Set when the servo is mounted such that increasing pulse rotates "
            "the mast counter-clockwise (i.e. opposite to the CW-positive "
            "convention in INTERFACES.md §0). Negates the angle on every "
            "servo MOVE so a +10° command produces +10° physical bearing. "
            "Pair with rfmesh-servo-calibrate --inverted if the same flag was "
            "needed at calibration time."
        ),
    )
    # SDR
    p.add_argument("--center-freq-hz", type=float, required=True, help="RTL-SDR tune frequency.")
    p.add_argument("--sample-rate-hz", type=float, default=2_048_000.0, help="RTL-SDR sample rate.")
    p.add_argument(
        "--gain-db", type=float, default=40.0, help="RTL-SDR gain (use number, not auto)."
    )
    p.add_argument(
        "--rtl-serial",
        default=None,
        help="Optional RTL-SDR serial; default = first device rtl_sdr finds.",
    )
    # Sweep
    p.add_argument(
        "--sweep-min-deg",
        type=float,
        default=None,
        help=(
            "Sweep start (geographic frame, degrees). If omitted, auto-derived "
            "from the servo's NVS calibration: angle_min + safety margin."
        ),
    )
    p.add_argument(
        "--sweep-max-deg",
        type=float,
        default=None,
        help=(
            "Sweep end (geographic frame, degrees). If omitted, auto-derived "
            "from the servo's NVS calibration: angle_max - safety margin."
        ),
    )
    p.add_argument(
        "--sweep-safety-margin-deg",
        type=float,
        default=2.0,
        help=(
            "Margin to shrink the auto-derived sweep range by, on each side, "
            "to keep the mast off the mechanical stops (default 2.0 deg). "
            "Only used when --sweep-min-deg or --sweep-max-deg is omitted."
        ),
    )
    p.add_argument("--sweep-step-deg", type=float, default=5.0, help="Sweep angular step.")
    p.add_argument(
        "--dwell-samples",
        type=int,
        default=32_768,
        help="IQ samples per heading dwell (default 32768 ≈ 16 ms at 2.048 MS/s).",
    )
    p.add_argument(
        "--inter-sweep-s",
        type=float,
        default=0.5,
        help="Idle delay between consecutive sweeps (default 0.5 s).",
    )
    # Node identity (carried onto every BearingReport)
    p.add_argument("--node-id", default="node-bench-01", help="Stable node identifier.")
    p.add_argument("--lat-deg", type=float, default=0.0, help="Node latitude (WGS-84).")
    p.add_argument("--lon-deg", type=float, default=0.0, help="Node longitude (WGS-84).")
    p.add_argument("--hae-m", type=float, default=0.0, help="Node height above WGS-84 ellipsoid.")
    p.add_argument(
        "--position-sigma-m",
        type=float,
        default=5.0,
        help="Node position 1-sigma uncertainty (m).",
    )
    p.add_argument(
        "--node-heading-deg",
        type=float,
        default=0.0,
        help="Antenna boresight when servo is at angle 0 (degrees true, [0,360)).",
    )
    # Web
    p.add_argument("--ws-host", default="127.0.0.1", help="WebSocket bind host.")
    p.add_argument("--ws-port", type=int, default=9001, help="WebSocket bind port.")
    p.add_argument("--ws-path", default="/dashboard", help="WebSocket URL path.")
    # Misc
    p.add_argument(
        "--print-bearings",
        action="store_true",
        help="Also print emitted BearingReports to stdout (one JSON-ish line each).",
    )
    p.add_argument(
        "--peak-prominence-db-min",
        type=float,
        default=6.0,
        help="Min peak-vs-noise-floor prominence for estimate() to emit (default 6 dB).",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return p


# ---------------------------------------------------------------------------
# Hardware-context managers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _open_servo(port: str) -> AsyncIterator[ServoDriver]:
    """Open the servo over USB-CDC and run the unsolicited-PONG handshake."""
    transport = SerialTransport(port)
    driver = ServoDriver(transport, own_transport=True)
    driver.__enter__()
    try:
        info = driver.connect()
        _LOG.info(
            "servo: connected to fw %u.%u.%u sha=%.8s (axes=%u)",
            info.fw_major,
            info.fw_minor,
            info.fw_patch,
            info.git_short_sha,
            info.axis_count,
        )
        yield driver
    finally:
        driver.__exit__(None, None, None)


def _build_sdr_node_config(args: argparse.Namespace) -> NodeConfig:
    """Build a minimal NodeConfig for RTLSDRDevice.configure().

    Only `config.sdr` is read by RTLSDRDevice; the rest is schema-required
    plumbing.
    """
    return NodeConfig(
        node_id=args.node_id,
        position=GeodeticPosition(
            lat_deg=args.lat_deg,
            lon_deg=args.lon_deg,
            hae_m=args.hae_m,
            sigma_m=args.position_sigma_m,
        ),
        heading_deg=args.node_heading_deg,
        sdr=SDRConfig(
            driver="rtlsdr",
            center_freq_hz=args.center_freq_hz,
            sample_rate_hz=args.sample_rate_hz,
            gain_db=args.gain_db,
            bias_tee=False,
            serial=args.rtl_serial,
        ),
        capabilities=(Capability.L1_RSSI,),
        bearer=BearerConfig(kind=BearerKind.WIFI),
        # Pydantic AnyUrl accepts string at construction (validated to URL);
        # mypy's stricter view of the field type does not see the coercion.
        fusion_endpoint="udp://127.0.0.1:9000",  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Trapezoidal motion profile
# ---------------------------------------------------------------------------


async def _move_servo_smooth(
    servo: ServoDriver,
    axis: int,
    current_deg: float,
    target_deg: float,
    *,
    max_vel_dps: float,
    accel_time_s: float,
    min_step_deg: float,
    fast_threshold_deg: float = 10.0,
    fast_settle_s: float = 0.10,
    stop: asyncio.Event | None = None,
    invert_direction: bool = False,
) -> float:
    """Drive the servo from current_deg to target_deg with a two-tier policy.

    * **Fast path (|delta| <= fast_threshold_deg, default 10°):** one MOVE
      to the target plus a fixed ``fast_settle_s`` dwell. No ramp. Small
      moves carry little momentum; ramping each per-step sweep leg makes
      the loop drag without protecting anything.
    * **Ramped path (|delta| > fast_threshold_deg):** trapezoidal motion
      profile. Discretises the move into ``min_step_deg``-sized angle
      steps; each step's delay is derived from the instantaneous
      velocity at that point in the accel / cruise / decel profile:

          v(s) = sqrt(2 * a * s)               in the accel region
          v(s) = v_max                         in the cruise region
          v(s) = sqrt(2 * a * (D - s))         in the decel region

      where ``a = v_max / accel_time``, ``D`` is the total angular
      distance, and ``s`` is the distance travelled so far. If the move
      is short enough that the trapezoid degenerates (i.e. ``D <
      v_max * accel_time``), the profile folds into a triangular
      accel-then-decel curve with reduced peak velocity.

    Args:
        servo: Open ServoDriver.
        axis: Axis to drive.
        current_deg: The servo's currently-commanded angle.
        target_deg: Where the move ends.
        max_vel_dps: Cruise angular velocity, degrees per second.
        accel_time_s: Ramp duration each side of cruise.
        min_step_deg: Discretisation step. Smaller = smoother + more USB traffic.
        fast_threshold_deg: Moves smaller than this take the fast path.
            Set to 0.0 to disable fast path (always ramp).
        fast_settle_s: Dwell after a fast-path MOVE.
        stop: Optional asyncio.Event to allow mid-move abort.
        invert_direction: If True, negate the angle sent to ``servo.move()`` so
            an inverted-mount servo (CCW-positive vs the CW-positive convention)
            still produces correct geographic bearing. Caller-visible angles
            stay in the geographic frame.

    Returns:
        The final commanded angle in the geographic frame
        (== target_deg unless aborted). The angle written to the servo is
        negated when ``invert_direction`` is True.
    """
    if max_vel_dps <= 0.0:
        msg = f"max_vel_dps must be > 0 (got {max_vel_dps})."
        raise ValueError(msg)
    if accel_time_s < 0.0:
        msg = f"accel_time_s must be >= 0 (got {accel_time_s})."
        raise ValueError(msg)
    if min_step_deg <= 0.0:
        msg = f"min_step_deg must be > 0 (got {min_step_deg})."
        raise ValueError(msg)

    delta = target_deg - current_deg
    distance = abs(delta)
    if distance < _MOTION_NOOP_DEG:
        return current_deg

    # Fast path: small moves get a single MOVE + fixed settle.
    if distance <= fast_threshold_deg:
        servo_angle = -target_deg if invert_direction else target_deg
        servo.move(axis, servo_angle)
        await asyncio.sleep(fast_settle_s)
        return target_deg

    direction = 1.0 if delta > 0.0 else -1.0
    n_steps = max(1, math.ceil(distance / min_step_deg))
    step = distance / n_steps  # actual per-iteration step (<= min_step_deg)

    # Ramp distance, capped to half the total (triangular case).
    accel = max_vel_dps / accel_time_s if accel_time_s > 0.0 else float("inf")
    ramp_dist_full = 0.5 * max_vel_dps * accel_time_s
    ramp_dist = min(ramp_dist_full, distance / 2.0)
    # Peak velocity actually reached (== max_vel for trapezoid, less for triangle).
    peak_vel = max_vel_dps if ramp_dist >= ramp_dist_full else math.sqrt(2.0 * accel * ramp_dist)
    min_vel_floor = 1.0  # dps; prevents zero-velocity divide-by-zero at endpoints.

    new_angle = current_deg
    for k in range(1, n_steps + 1):
        if stop is not None and stop.is_set():
            return new_angle
        # Use midpoint of this step's distance interval for the velocity sample.
        s_mid = (k - 0.5) * step
        if s_mid < ramp_dist:
            # Accel phase.
            v = math.sqrt(2.0 * accel * s_mid)
        elif s_mid > distance - ramp_dist:
            # Decel phase.
            v = math.sqrt(2.0 * accel * (distance - s_mid))
        else:
            # Cruise.
            v = peak_vel
        v = max(v, min_vel_floor)
        delay = step / v
        new_angle = current_deg + direction * k * step
        servo_angle = -new_angle if invert_direction else new_angle
        servo.move(axis, servo_angle)
        await asyncio.sleep(delay)
    return new_angle


# ---------------------------------------------------------------------------
# Sweep loop
# ---------------------------------------------------------------------------


async def _run_sweep_loop(
    args: argparse.Namespace,
    servo: ServoDriver,
    sdr: RTLSDRDevice,
    estimator: L1AmplitudeSweepEstimator,
    pubsub: DashboardPubSub,
    stop: asyncio.Event,
) -> None:
    """Drive servo through configured arc; observe IQ at each heading.

    One sweep emits one BearingReport (or None if the sweep is too flat
    for honest peak-fit; refusal reason is logged but not silenced).
    """
    sweep_min = float(args.sweep_min_deg)
    sweep_max = float(args.sweep_max_deg)
    sweep_step = float(args.sweep_step_deg)
    settle_s = float(args.servo_settle_s)
    dwell_n = int(args.dwell_samples)
    inter_s = float(args.inter_sweep_s)
    boresight_offset = float(args.node_heading_deg)
    max_vel = float(args.servo_max_vel_dps)
    accel_s = float(args.servo_accel_time_s)
    motion_step = float(args.servo_motion_step_deg)
    fast_thresh = float(args.servo_fast_threshold_deg)
    fast_settle = float(args.servo_fast_settle_s)
    invert = bool(args.invert_direction)

    # Numeric angles (mechanical, servo frame); convert to geographic on observe().
    n_steps = round((sweep_max - sweep_min) / sweep_step) + 1
    angles_mech = np.linspace(sweep_min, sweep_max, n_steps).tolist()

    _LOG.info(
        "sweep loop: %d angles from %.1f deg to %.1f deg step %.1f deg, dwell=%d samples; "
        "motion: max_vel=%.1f dps, accel=%.2f s, profile_step=%.2f deg, "
        "fast_threshold=%.1f deg, fast_settle=%.2f s",
        len(angles_mech),
        sweep_min,
        sweep_max,
        sweep_step,
        dwell_n,
        max_vel,
        accel_s,
        motion_step,
        fast_thresh,
        fast_settle,
    )

    # Initialise current angle from firmware-reported position, or fall back
    # to sweep_min if the servo has never moved since boot. The firmware
    # reports the angle in its own (calibration-table) frame; convert to the
    # geographic frame when --invert-direction is set.
    pos = servo.position(args.axis)
    if pos.ms_since_move == _NEVER_MOVED_SENTINEL:
        _LOG.info(
            "servo: never moved since boot — smooth move to sweep start %.1f deg from 0 deg "
            "(invert_direction=%s)",
            sweep_min,
            invert,
        )
        current_angle = 0.0
    else:
        fw_angle = float(pos.commanded_angle_deg)
        current_angle = -fw_angle if invert else fw_angle
        _LOG.info(
            "servo: starting from commanded angle %.2f deg "
            "(firmware-frame %.2f, %.0f ms since last move; invert_direction=%s)",
            current_angle,
            fw_angle,
            float(pos.ms_since_move),
            invert,
        )

    # Park at sweep_min before the first observation so every sweep starts
    # from the same direction (avoids inconsistent gear-backlash signatures).
    current_angle = await _move_servo_smooth(
        servo,
        args.axis,
        current_angle,
        sweep_min,
        max_vel_dps=max_vel,
        accel_time_s=accel_s,
        min_step_deg=motion_step,
        fast_threshold_deg=fast_thresh,
        fast_settle_s=fast_settle,
        stop=stop,
        invert_direction=invert,
    )
    await asyncio.sleep(settle_s)

    sweep_count = 0
    while not stop.is_set():
        sweep_count += 1
        t_unix_ns = time.time_ns()
        estimator.begin_sweep(t_unix_ns)
        for angle_mech in angles_mech:
            if stop.is_set():
                break
            current_angle = await _move_servo_smooth(
                servo,
                args.axis,
                current_angle,
                float(angle_mech),
                max_vel_dps=max_vel,
                accel_time_s=accel_s,
                min_step_deg=motion_step,
                fast_threshold_deg=fast_thresh,
                fast_settle_s=fast_settle,
                stop=stop,
                invert_direction=invert,
            )
            await asyncio.sleep(settle_s)
            # Off-thread the blocking read so the WS server keeps responding.
            samples = await asyncio.to_thread(sdr.read, dwell_n)
            # Mechanical → geographic: heading_deg in [0, 360).
            heading_geo = (boresight_offset + float(angle_mech)) % 360.0
            estimator.observe(heading_geo, samples)

        # Pass any IQBlock; L1.estimate ignores `samples` and reads from
        # the accumulator. We pass the last dwell to satisfy the Protocol.
        report = estimator.estimate(samples)
        if report is None:
            _LOG.warning(
                "sweep #%d: estimator refused — %s",
                sweep_count,
                estimator.last_refusal_reason,
            )
        else:
            _LOG.info(
                "sweep #%d: azimuth=%.2f deg sigma=%.2f deg snr=%.1f dB",
                sweep_count,
                report.azimuth_deg,
                report.azimuth_sigma_deg,
                report.snr_db if report.snr_db is not None else float("nan"),
            )
            if args.print_bearings:
                print(
                    f"BEARING t={report.t_unix_ns} node={report.node_id} "
                    f"az={report.azimuth_deg:.2f} sigma={report.azimuth_sigma_deg:.2f} "
                    f"snr={report.snr_db}",
                    flush=True,
                )
            await pubsub.publish_bearing(report)

        if stop.is_set():
            break
        await asyncio.sleep(inter_s)


# ---------------------------------------------------------------------------
# WebSocket server
# ---------------------------------------------------------------------------


def _build_ws_app(pubsub: DashboardPubSub, ws_path: str) -> web.Application:
    """Build the aiohttp app exposing a single WebSocket endpoint."""

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=10.0)
        await ws.prepare(request)
        sub = WebSocketSubscriber(ws)
        pubsub.add_subscriber(sub)
        peer = request.transport.get_extra_info("peername") if request.transport else None
        _LOG.info("ws: client connected (%s); subs=%d", peer, pubsub.subscriber_count)
        try:
            # Drain client-side messages (we ignore them) so the WS lives.
            async for msg in ws:
                if msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                    break
        finally:
            pubsub.remove_subscriber(sub)
            _LOG.info("ws: client disconnected (%s); subs=%d", peer, pubsub.subscriber_count)
        return ws

    app = web.Application()
    app.add_routes([web.get(ws_path, ws_handler)])
    return app


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _amain(args: argparse.Namespace) -> int:  # noqa: PLR0912, PLR0915
    pubsub = DashboardPubSub()
    stop = asyncio.Event()

    # SIGINT / SIGTERM → graceful shutdown.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # WebSocket server.
    app = _build_ws_app(pubsub, args.ws_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.ws_host, args.ws_port)
    await site.start()
    _LOG.info(
        "ws: serving on ws://%s:%d%s — connect with: "
        "uv run rfmesh-ops --connect ws://%s:%d%s --layout debug",
        args.ws_host,
        args.ws_port,
        args.ws_path,
        args.ws_host,
        args.ws_port,
        args.ws_path,
    )

    # Hardware up.
    sdr_node_config = _build_sdr_node_config(args)
    sdr = RTLSDRDevice(serial=args.rtl_serial)
    sdr.open()
    sdr.configure(sdr_node_config)
    caps = sdr.capabilities()
    _LOG.info(
        "sdr: opened (driver=%s, actual_rate=%.0f Hz, freq=%.3f MHz, gain=%s, power_cal=%s)",
        caps.driver,
        caps.actual_sample_rate_hz,
        args.center_freq_hz / 1e6,
        args.gain_db,
        caps.is_power_calibrated,
    )

    estimator = L1AmplitudeSweepEstimator(
        node_id=args.node_id,
        node_position=sdr_node_config.position,
        sweep_step_deg=args.sweep_step_deg,
        sweep_dwell_samples=args.dwell_samples,
        peak_prominence_db_min=args.peak_prominence_db_min,
    )

    try:
        async with _open_servo(args.servo_port) as servo:
            cal = servo.get_calibration(args.axis)
            invert = bool(args.invert_direction)
            # Map firmware-frame calibration limits to the geographic frame
            # the operator's sweep/park flags use. When inverted, geographic
            # = -firmware, so the geographic range is [-angle_max, -angle_min].
            if invert:
                geo_min = -cal.angle_max_deg
                geo_max = -cal.angle_min_deg
            else:
                geo_min = cal.angle_min_deg
                geo_max = cal.angle_max_deg
            _LOG.info(
                "servo: axis %d calibrated pulse=[%u, %u]us angle_fw=[%.1f, %.1f] deg "
                "angle_geo=[%.1f, %.1f] deg (invert_direction=%s)",
                args.axis,
                cal.pulse_min_us,
                cal.pulse_max_us,
                cal.angle_min_deg,
                cal.angle_max_deg,
                geo_min,
                geo_max,
                invert,
            )
            # Auto-derive sweep range from calibration if the operator did
            # not pass --sweep-min-deg / --sweep-max-deg. Shrink by the
            # safety margin to keep the mast off the mechanical stops.
            margin = float(args.sweep_safety_margin_deg)
            if args.sweep_min_deg is None:
                args.sweep_min_deg = geo_min + margin
                _LOG.info(
                    "sweep-min-deg auto-derived: %.2f deg "
                    "(calibrated geo_min %.2f + %.2f deg margin)",
                    args.sweep_min_deg,
                    geo_min,
                    margin,
                )
            if args.sweep_max_deg is None:
                args.sweep_max_deg = geo_max - margin
                _LOG.info(
                    "sweep-max-deg auto-derived: %.2f deg "
                    "(calibrated geo_max %.2f - %.2f deg margin)",
                    args.sweep_max_deg,
                    geo_max,
                    margin,
                )
            if args.sweep_min_deg >= args.sweep_max_deg:
                _LOG.error(
                    "sweep range collapsed: min=%.2f >= max=%.2f. "
                    "Calibrated geographic range is [%.2f, %.2f] and the safety "
                    "margin is %.2f deg each side. Re-calibrate or pass explicit "
                    "--sweep-min-deg / --sweep-max-deg.",
                    args.sweep_min_deg,
                    args.sweep_max_deg,
                    geo_min,
                    geo_max,
                    margin,
                )
                return 2

            # Sanity-check the requested sweep fits inside calibrated limits
            # (in the geographic frame the operator commands in).
            if args.sweep_min_deg < geo_min or args.sweep_max_deg > geo_max:
                _LOG.error(
                    "requested sweep [%.1f, %.1f] deg exceeds calibrated geographic "
                    "range [%.1f, %.1f] deg; re-run rfmesh-servo-calibrate (with "
                    "--inverted if needed) or narrow --sweep-{min,max}-deg",
                    args.sweep_min_deg,
                    args.sweep_max_deg,
                    geo_min,
                    geo_max,
                )
                return 2

            sweep_task = asyncio.create_task(
                _run_sweep_loop(args, servo, sdr, estimator, pubsub, stop),
                name="sweep-loop",
            )
            await stop.wait()
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task

            # Smooth park-on-shutdown. The park motion intentionally does
            # NOT take a stop event (the operator wants the mast to reach
            # the park angle, not freeze halfway through it).
            if args.servo_park_deg is not None:
                park_target = float(args.servo_park_deg)
                if geo_min <= park_target <= geo_max:
                    pos = servo.position(args.axis)
                    if pos.ms_since_move == _NEVER_MOVED_SENTINEL:
                        park_from = 0.0
                    else:
                        fw_angle = float(pos.commanded_angle_deg)
                        park_from = -fw_angle if invert else fw_angle
                    _LOG.info(
                        "shutdown: smooth-park axis %d from %.2f deg to %.2f deg "
                        "(geographic frame)",
                        args.axis,
                        park_from,
                        park_target,
                    )
                    await _move_servo_smooth(
                        servo,
                        args.axis,
                        park_from,
                        park_target,
                        max_vel_dps=float(args.servo_max_vel_dps),
                        accel_time_s=float(args.servo_accel_time_s),
                        min_step_deg=float(args.servo_motion_step_deg),
                        fast_threshold_deg=float(args.servo_fast_threshold_deg),
                        fast_settle_s=float(args.servo_fast_settle_s),
                        stop=None,
                        invert_direction=invert,
                    )
                else:
                    _LOG.warning(
                        "shutdown: --servo-park-deg %.1f deg outside calibrated "
                        "geographic range [%.1f, %.1f] deg; skipping park",
                        park_target,
                        geo_min,
                        geo_max,
                    )
    finally:
        _LOG.info("cleanup: closing SDR + WS server")
        try:
            sdr.close()
        except Exception:
            _LOG.exception("sdr.close() raised")
        await runner.cleanup()

    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
