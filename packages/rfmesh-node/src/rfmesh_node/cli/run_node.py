"""``rfmesh-node`` CLI entry point.

Loads a ``NodeConfig`` from YAML, constructs the appropriate receiver,
wires up a bearer, and runs ``Node.run()`` until SIGINT.

Receiver factory (``--config`` ``sdr.driver``):
  * ``"rtlsdr"`` -- real RTL-SDR V4 via the salvaged ``RTLSDRDevice``
    (single-channel; the L1 servo-sweep path). This is the field node.
  * ``"sim"`` -- still raises: the bare ``SyntheticReceiver`` needs a
    ``SimulationScenario``; use ``rfmesh-demo-replay`` for sim runs.
  * any other driver -- raises ``NotImplementedError`` with a loud,
    specific message (no silent down-fall, Invariant B3).

Bearer is chosen by the ``fusion_endpoint`` URL scheme, *not* by a
contract change: ``http(s)://`` -> ``HttpBearer`` (POSTs JSON straight to
the both3 backend ``/bearings``), ``udp://`` -> the salvaged ``WifiBearer``
(/ ``LoraBearer`` / ``BothBearer`` per ``bearer.kind``).

When a ``--servo-port`` is given and ``L1_RSSI`` is an active capability,
an ``L1SweepLoop`` is wired in -- servo + RTL-SDR + L1 estimator + bearer
-- so the node actually produces and ships bearings (the v1.0 ``Node``
container otherwise only runs the heartbeat).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError
from rfmesh_contracts import BearerKind, Capability, NodeConfig

from ..bearer import BothBearer, HttpBearer, LoraBearer, WifiBearer
from ..l1_sweep import L1SweepConfig, L1SweepLoop
from ..node import Node
from ..runtime import CapabilityMismatchError

if TYPE_CHECKING:
    from rfmesh_contracts import Bearer, Receiver


_LOG = logging.getLogger(__name__)


def _build_receiver(config: NodeConfig) -> Receiver:
    """Build a receiver for the given config.

    ``rtlsdr`` -> real RTL-SDR (the field L1 path). ``sim`` and other
    drivers raise a loud, specific error (Invariant B3: no silent
    fallback to the wrong receiver).
    """
    driver = config.sdr.driver
    if driver == "rtlsdr":
        # Imported lazily so a sim/bench host without rfmesh-sdr's runtime
        # deps can still import this module; the field node has it.
        from rfmesh_sdr.devices.rtlsdr import RTLSDRDevice  # noqa: PLC0415

        return RTLSDRDevice(serial=config.sdr.serial)
    if driver == "sim":
        msg = (
            "run_node: scenario-less simulator receivers are not constructible "
            "from a bare NodeConfig (the simulator requires a SimulationScenario). "
            "Use rfmesh-demo-replay to run with a scenario; for bench tests, "
            "construct Node(...) with an explicit Receiver."
        )
        raise NotImplementedError(msg)
    msg = (
        f"run_node: SDR driver {driver!r} is not yet wired into the rfmesh-node "
        "CLI. Supported: 'rtlsdr'. Use rfmesh-demo-replay for scenario-driven "
        "runs, or construct Node(...) with an explicit Receiver."
    )
    raise NotImplementedError(msg)


def _build_bearer(config: NodeConfig) -> Bearer:
    """Build a bearer chosen by the ``fusion_endpoint`` URL scheme.

    ``http(s)://`` -> ``HttpBearer`` (POSTs JSON to the both3 backend).
    ``udp://`` -> ``WifiBearer`` / ``LoraBearer`` / ``BothBearer`` per
    ``bearer.kind`` (the salvaged UDP path).

    ``BearerConfig._lora_needs_port`` guarantees ``lora_serial_port`` is
    set for LoRa/Both; the ``None`` guard is a mypy narrower + defence
    against a future refactor dropping that validator.
    """
    endpoint = str(config.fusion_endpoint)
    scheme = urlparse(endpoint).scheme
    if scheme in ("http", "https"):
        return HttpBearer(endpoint)

    bearer_cfg = config.bearer
    if bearer_cfg.kind is BearerKind.WIFI:
        return WifiBearer(endpoint)
    port = bearer_cfg.lora_serial_port
    if port is None:
        msg = (
            f"BearerConfig.kind={bearer_cfg.kind.value} requires lora_serial_port; "
            "_lora_needs_port validator must have been bypassed."
        )
        raise ValueError(msg)
    if bearer_cfg.kind is BearerKind.LORA:
        return LoraBearer(port)
    # BOTH -- both halves.
    return BothBearer(WifiBearer(endpoint), LoraBearer(port))


def _build_sweep_loop(
    config: NodeConfig,
    receiver: Receiver,
    bearer: Bearer,
    args: argparse.Namespace,
) -> L1SweepLoop | None:
    """Build the L1 sweep loop, or ``None`` if not applicable.

    Returns ``None`` (heartbeat-only node) unless ``--servo-port`` is set
    AND ``L1_RSSI`` is declared. ``heading_deg`` is required for the L1
    sweep (the boresight the servo angle is relative to); a missing one is
    a loud config error rather than a silent zero.
    """
    if args.servo_port is None:
        return None
    if Capability.L1_RSSI not in config.capabilities:
        return None
    if config.heading_deg is None:
        msg = (
            "run_node: L1 sweep requires NodeConfig.heading_deg (antenna "
            "boresight azimuth, set by survey-and-align); it is None."
        )
        raise ValueError(msg)

    from rfmesh_servo.driver import ServoDriver  # noqa: PLC0415
    from rfmesh_servo.transport import SerialTransport  # noqa: PLC0415

    servo = ServoDriver(SerialTransport(args.servo_port), own_transport=True)
    sweep_cfg = L1SweepConfig(
        min_deg=args.sweep_min_deg,
        max_deg=args.sweep_max_deg,
        step_deg=args.sweep_step_deg,
        settle_s=args.settle_ms / 1000.0,
        dwell_samples=args.dwell_samples,
        inter_sweep_s=args.inter_sweep_s,
    )
    return L1SweepLoop(
        receiver=receiver,
        bearer=bearer,
        servo=servo,
        node_id=config.node_id,
        node_position=config.position,
        boresight_heading_deg=config.heading_deg,
        config=sweep_cfg,
    )


async def _run(config: NodeConfig, args: argparse.Namespace) -> None:
    """Bring the node up; wait for SIGINT; tear down."""
    receiver = _build_receiver(config)
    bearer = _build_bearer(config)
    sweep_loop = _build_sweep_loop(config, receiver, bearer, args)
    node = Node(config, receiver=receiver, bearer=bearer, sweep_loop=sweep_loop)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler is unsupported on Windows; the CLI is
        # Linux-targeted, so we suppress the NotImplementedError there.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(node.shutdown()))

    await node.run()


def run_node_main(argv: list[str] | None = None) -> int:  # noqa: PLR0911
    """``rfmesh-node`` CLI -- returns a process exit code.

    Multiple returns (one per distinct error class) are intentional --
    each gives the operator a specific, actionable log message instead
    of a generic "config failed" string (demo-integrity council R5).
    """
    parser = argparse.ArgumentParser(prog="rfmesh-node")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to a NodeConfig YAML",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="logging level (DEBUG/INFO/WARNING/ERROR)",
    )
    parser.add_argument(
        "--servo-port",
        type=str,
        default=None,
        help=(
            "serial port of the ESP32 servo controller (e.g. /dev/ttyACM0 or "
            "COM5). Enables the L1 sweep when set and L1_RSSI is declared."
        ),
    )
    parser.add_argument(
        "--sweep-min-deg",
        type=float,
        default=-90.0,
        help="servo sweep arc start, degrees relative to boresight (default -90)",
    )
    parser.add_argument(
        "--sweep-max-deg",
        type=float,
        default=90.0,
        help=(
            "servo sweep arc end, degrees relative to boresight (default +90). "
            "Arc must exceed the antenna HPBW so the off-axis floor is real."
        ),
    )
    parser.add_argument(
        "--sweep-step-deg",
        type=float,
        default=2.0,
        help="servo sweep step, degrees (default 2.0)",
    )
    parser.add_argument(
        "--settle-ms",
        type=float,
        default=200.0,
        help="dwell after each servo move before sampling, ms (default 200)",
    )
    parser.add_argument(
        "--dwell-samples",
        type=int,
        default=1024,
        help="IQ samples read per heading (default 1024)",
    )
    parser.add_argument(
        "--inter-sweep-s",
        type=float,
        default=1.0,
        help="pause between sweeps, seconds (default 1.0)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper())

    # Operator-facing error UX (demo-integrity council R5): YAML
    # parse errors, schema-validation errors and capability mismatches
    # are caught at the CLI boundary and rendered as `_LOG.error(...)`
    # with a non-zero exit code, instead of dumping a Python traceback
    # at the operator.
    try:
        with args.config.open("r", encoding="utf-8") as fp:
            payload = yaml.safe_load(fp)
        config = NodeConfig.model_validate(payload)
    except FileNotFoundError as exc:
        _LOG.error("rfmesh-node: config not found: %s", exc)
        return 2
    except yaml.YAMLError as exc:
        _LOG.error("rfmesh-node: malformed config YAML: %s", exc)
        return 2
    except ValidationError as exc:
        _LOG.error("rfmesh-node: config schema invalid:\n%s", exc)
        return 2

    try:
        asyncio.run(_run(config, args))
    except CapabilityMismatchError as exc:
        _LOG.error("rfmesh-node: capability mismatch: %s", exc)
        return 2
    except NotImplementedError as exc:
        _LOG.error("rfmesh-node: %s", exc)
        return 2
    except ValueError as exc:
        # _build_bearer ValueError when LoRa port missing despite validator
        # (defensive guard against future refactor dropping _lora_needs_port).
        _LOG.error("rfmesh-node: bearer build failed: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_node_main())
