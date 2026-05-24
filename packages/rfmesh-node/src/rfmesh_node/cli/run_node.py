"""``rfmesh-node`` CLI entry point.

ADR-022 (PROPOSED 2026-05-23): the CLI shrinks to ``--config`` (path to a
single ``NodeRuntimeConfig`` YAML) and ``--log-level``. Every prior
per-knob flag (``--servo-port``, ``--sweep-*``, ``--peer-*``,
``--rendezvous-*``) has moved into the YAML so the runtime config is one
file. Soldier-facing operation does not use this CLI at all -- the field
node boots into systemd and phones home over the comms-mode command
channel; ``link.html`` is the canonical operator surface (ADR-021).

CLI scope: bench / admin / debug.

Receiver factory (``sdr.driver`` in YAML):
  * ``"rtlsdr"`` -- real RTL-SDR V4 via the salvaged ``RTLSDRDevice``
    (single-channel; the L1 servo-sweep path). This is the field node.
  * ``"sim"`` -- raises: bare ``SyntheticReceiver`` needs a scenario;
    use ``rfmesh-demo-replay`` for sim runs.
  * any other driver -- raises ``NotImplementedError`` with a specific
    message (no silent down-fall, B3).

Bearer is chosen by ``node.fusion_endpoint`` URL scheme: ``http(s)://``
-> ``HttpBearer`` (the soldier-facing default), ``udp://`` -> WifiBearer
/ LoraBearer / BothBearer per ``node.bearer.kind``.

When ``servo_port`` is set in YAML and ``L1_RSSI`` is an active
capability, an ``L1SweepLoop`` is wired in. A ``rendezvous:`` block
additionally enables ADR-019 directional rendezvous. A
``command_endpoint:`` block with ``enabled=true`` wires in the
``CommandChannel`` so the node phones the backend and the operator can
see / steer it from ``link.html``.

Legacy flags (--servo-port, --sweep-*, --peer-*, --rendezvous-*) are
refused with an actionable message naming the YAML key each one maps
to. Set ``RFMESH_NODE_ALLOW_LEGACY_FLAGS=1`` to demote the refusal to a
DeprecationWarning for one release (per ADR-022 backwards-compat window).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import warnings
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError
from rfmesh_contracts import BearerKind, Capability

from ..bearer import BothBearer, HttpBearer, LoraBearer, WifiBearer
from ..l1_sweep import L1SweepLoop
from ..node import Node
from ..rendezvous import RendezvousLoop
from ..runtime import CapabilityMismatchError
from ..runtime_config import NodeRuntimeConfig

if TYPE_CHECKING:
    from rfmesh_contracts import Bearer, Receiver
    from rfmesh_servo.driver import ServoDriver


_LOG = logging.getLogger(__name__)

# Legacy flag names -> YAML key path. Pre-ADR-022 the CLI accepted 16
# flags; the deprecation window (ADR-022 §"Backwards-compat") keeps the
# parser aware of them so we can emit an actionable error / warning.
_LEGACY_FLAGS: dict[str, str] = {
    "--servo-port": "servo_port",
    "--sweep-min-deg": "sweep.min_deg",
    "--sweep-max-deg": "sweep.max_deg",
    "--sweep-step-deg": "sweep.step_deg",
    "--settle-ms": "sweep.settle_s",
    "--dwell-samples": "sweep.dwell_samples",
    "--inter-sweep-s": "sweep.inter_sweep_s",
    "--peer-id": "rendezvous.peer_node_id",
    "--peer-lat": "rendezvous.peer.lat_deg",
    "--peer-lon": "rendezvous.peer.lon_deg",
    "--peer-hae-m": "rendezvous.peer.hae_m",
    "--peer-sigma-m": "rendezvous.peer.sigma_m",
    "--rendezvous-refine-half-arc-deg": "rendezvous.refine_half_arc_deg",
    "--rendezvous-link-hold-s": "rendezvous.link_hold_s",
}


def _build_receiver(runtime: NodeRuntimeConfig) -> Receiver:
    """Build a receiver from the YAML ``node.sdr`` block.

    ``rtlsdr`` -> real RTL-SDR (the field L1 path). ``sim`` and other
    drivers raise loudly (B3).
    """
    driver = runtime.node.sdr.driver
    if driver == "rtlsdr":
        # Lazy import: a sim/bench host without rfmesh-sdr's runtime
        # deps can still import this module; the field node has it.
        from rfmesh_sdr.devices.rtlsdr import RTLSDRDevice  # noqa: PLC0415

        return RTLSDRDevice(serial=runtime.node.sdr.serial)
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


def _build_bearer(runtime: NodeRuntimeConfig) -> Bearer:
    """Build a bearer chosen by ``node.fusion_endpoint``'s URL scheme.

    ``http(s)://`` -> ``HttpBearer`` (POSTs JSON to the both3 backend).
    ``udp://`` -> ``WifiBearer`` / ``LoraBearer`` / ``BothBearer`` per
    ``node.bearer.kind``.
    """
    endpoint = str(runtime.node.fusion_endpoint)
    scheme = urlparse(endpoint).scheme
    if scheme in ("http", "https"):
        return HttpBearer(endpoint)

    bearer_cfg = runtime.node.bearer
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
    return BothBearer(WifiBearer(endpoint), LoraBearer(port))


def _build_servo(runtime: NodeRuntimeConfig) -> ServoDriver | None:
    """Build the (unconnected) servo driver, or ``None`` if not applicable.

    Returns ``None`` unless ``servo_port`` is set AND ``L1_RSSI`` is
    declared. ``Node`` owns the connect/close lifecycle (ADR-019).

    ``servo_port`` accepts two URL shapes:
      * ``"/dev/ttyACM0"`` (legacy) — opened via
        :class:`SerialTransport` (pyserial / USB-CDC).
      * ``"tcp://host:port"`` — opened via :class:`TcpTransport`
        (ESP32-C6 in WiFi-station mode, hosts a TCP server on
        port 5555 by default; bench-grade alternative to USB-CDC
        when USB enumeration / autosuspend / hub contention
        destabilises the link).
    """
    if runtime.servo_port is None:
        return None
    if Capability.L1_RSSI not in runtime.node.capabilities:
        return None

    from rfmesh_servo.driver import ServoDriver  # noqa: PLC0415
    from rfmesh_servo.transport import SerialTransport, TcpTransport  # noqa: PLC0415

    port_str = runtime.servo_port
    if port_str.startswith("tcp://"):
        from urllib.parse import urlparse  # noqa: PLC0415

        parsed = urlparse(port_str)
        host = parsed.hostname
        if not host:
            msg = (
                f"_build_servo: servo_port={port_str!r} is missing a hostname; "
                "expected 'tcp://host:port' (e.g. 'tcp://node-01.local:5555')."
            )
            raise ValueError(msg)
        tcp_port = parsed.port if parsed.port is not None else 5555
        transport = TcpTransport(host=host, port=tcp_port)
    else:
        transport = SerialTransport(port_str)
    return ServoDriver(transport, own_transport=True)


def _build_sweep_loop(
    runtime: NodeRuntimeConfig,
    receiver: Receiver,
    bearer: Bearer,
    servo: ServoDriver | None,
) -> L1SweepLoop | None:
    """Build the L1 sweep loop, or ``None`` if not applicable."""
    if servo is None:
        return None
    if runtime.node.heading_deg is None:
        msg = (
            "run_node: L1 sweep requires node.heading_deg in the YAML "
            "(antenna boresight azimuth, set by survey-and-align); it is None."
        )
        raise ValueError(msg)

    return L1SweepLoop(
        receiver=receiver,
        bearer=bearer,
        servo=servo,
        node_id=runtime.node.node_id,
        node_position=runtime.node.position,
        boresight_heading_deg=runtime.node.heading_deg,
        config=runtime.sweep_dataclass(),
    )


def _build_rendezvous_loop(
    runtime: NodeRuntimeConfig,
    receiver: Receiver,
    servo: ServoDriver | None,
) -> RendezvousLoop | None:
    """Build the rendezvous loop, or ``None`` if no ``rendezvous:`` block."""
    rv_dc = runtime.rendezvous_dataclass()
    if rv_dc is None:
        return None
    # ``NodeRuntimeConfig._coherence`` already refused this combination at
    # load time; the guards here are defence-in-depth.
    if servo is None:
        msg = "run_node: rendezvous needs a servo; servo_port must be set."
        raise ValueError(msg)
    if runtime.node.heading_deg is None:
        msg = (
            "run_node: rendezvous requires node.heading_deg in the YAML "
            "(antenna boresight azimuth, set by survey-and-align); it is None."
        )
        raise ValueError(msg)

    return RendezvousLoop(
        receiver=receiver,
        servo=servo,
        node_id=runtime.node.node_id,
        node_position=runtime.node.position,
        boresight_heading_deg=runtime.node.heading_deg,
        config=rv_dc,
    )


def _build_comms_loop(
    runtime: NodeRuntimeConfig,
    servo: object | None,
) -> tuple[object | None, object | None]:
    """Build the ADR-025 CommsLoop (+ its own RX) when comms mode is enabled.

    Returns ``(comms_loop, comms_receiver)``. The comms RX is a
    distinct ``LoopbackReceiver`` (not the same instance Node uses
    for DF -- DF and COMMS are mutex per ADR-025 Decision B and the
    YAML validator). For v1.3.0 the only supported driver is
    ``"sim"`` (LoopbackChannel + SyntheticTransmitter + LoopbackReceiver);
    hardware drivers (BladeRF / HackRF / Pluto TX) land in the
    parallel Iter 6 branch and slot in here without contract change.
    """
    if runtime.comms is None:
        return (None, None)
    driver = runtime.node.sdr.driver
    if driver != "sim":
        msg = (
            f"run_node: comms mode on driver {driver!r} is not yet wired "
            "into the CLI (hardware TX drivers are ADR-025 Iter 6 -- "
            "BladeRF / HackRF / Pluto+). Use driver=sim for the loopback "
            "smoke test, or wait for the hardware-driver branch to merge."
        )
        raise NotImplementedError(msg)
    if runtime.node.heading_deg is None:
        msg = (
            "run_node: comms mode requires node.heading_deg in the YAML "
            "(antenna boresight azimuth; the comms loop computes the "
            "servo angle that points the Yagi at the peer)."
        )
        raise ValueError(msg)
    # Lazy imports so non-comms nodes do not load the rfmesh-sdr
    # simulator / rfmesh-dsss DSP stack on every boot.
    from rfmesh_sdr.simulator import (  # noqa: PLC0415
        LoopbackChannel,
        LoopbackReceiver,
        SyntheticTransmitter,
    )

    from rfmesh_node.comms.comms_loop import CommsLoop  # noqa: PLC0415

    comms_contract = runtime.comms_contract()
    comms_link = runtime.comms_link_dataclass()
    if comms_contract is None or comms_link is None:
        msg = "run_node: comms section present but materialisers returned None (bug)."
        raise RuntimeError(msg)
    # One shared loopback channel for the sim path. Real hardware will
    # have separate TX / RX device handles instead.
    channel = LoopbackChannel(
        sample_rate_hz=comms_contract.chip_rate_hz,
    )
    tx = SyntheticTransmitter(channel)
    comms_rx = LoopbackReceiver(channel)
    loop = CommsLoop(
        self_node_id=runtime.node.node_id,
        self_position=runtime.node.position,
        boresight_heading_deg=runtime.node.heading_deg,
        comms_link=comms_link,
        comms_config=comms_contract,
        transmitter=tx,
        receiver=comms_rx,
        servo=servo,
    )
    return (loop, comms_rx)


async def _run(runtime: NodeRuntimeConfig) -> None:
    """Bring the node up; wait for SIGINT; tear down."""
    receiver = _build_receiver(runtime)
    bearer = _build_bearer(runtime)
    servo = _build_servo(runtime)
    sweep_loop = _build_sweep_loop(runtime, receiver, bearer, servo)
    rendezvous_loop = _build_rendezvous_loop(runtime, receiver, servo)
    comms_loop, _comms_rx = _build_comms_loop(runtime, servo)
    node = Node(
        runtime.node,
        receiver=receiver,
        bearer=bearer,
        sweep_loop=sweep_loop,
        rendezvous_loop=rendezvous_loop,
        comms_loop=comms_loop,  # type: ignore[arg-type]
        servo=servo,
        command_endpoint=runtime.command_endpoint if runtime.command_endpoint.enabled else None,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler is unsupported on Windows; the CLI is
        # Linux-targeted, so we suppress the NotImplementedError there.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(node.shutdown()))

    await node.run()


def _legacy_flag_check(argv: list[str]) -> None:
    """Refuse legacy CLI flags with an actionable error (ADR-022).

    If ``RFMESH_NODE_ALLOW_LEGACY_FLAGS=1`` is set, demote the refusal to
    a ``DeprecationWarning`` for one release. Otherwise raise ``SystemExit``.
    """
    hits: list[tuple[str, str]] = []
    for tok in argv:
        head = tok.split("=", 1)[0]
        yaml_key = _LEGACY_FLAGS.get(head)
        if yaml_key is not None:
            hits.append((head, yaml_key))
    if not hits:
        return
    bullet_lines = "\n".join(f"  {flag}  ->  YAML key {key!r}" for flag, key in hits)
    msg = (
        "rfmesh-node: per-knob CLI flags removed in ADR-022. Move these into "
        "the single YAML at --config:\n"
        f"{bullet_lines}\n"
        "See docs/adr/ADR-022-single-yaml-node-runtime-config.md or "
        "field-deploy/node-config.example.yaml for the YAML shape."
    )
    if os.environ.get("RFMESH_NODE_ALLOW_LEGACY_FLAGS") == "1":
        warnings.warn(msg, DeprecationWarning, stacklevel=2)
        _LOG.warning("%s", msg)
        return
    raise SystemExit(msg)


def run_node_main(argv: list[str] | None = None) -> int:  # noqa: PLR0911
    """``rfmesh-node`` CLI -- returns a process exit code.

    Many distinct returns (one per error class) is intentional -- each
    gives the operator a specific, actionable log message instead of a
    generic "config failed" string (demo-integrity council R5).
    """
    argv_list = list(argv) if argv is not None else None
    if argv_list is not None:
        _legacy_flag_check(argv_list)
    else:
        import sys  # noqa: PLC0415

        _legacy_flag_check(sys.argv[1:])

    parser = argparse.ArgumentParser(
        prog="rfmesh-node",
        description=(
            "Bench/admin CLI for one rfmesh field node. Soldier-facing operation "
            "boots via systemd (field-deploy/) and is controlled from link.html."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to a NodeRuntimeConfig YAML (see ADR-022)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="logging level (DEBUG/INFO/WARNING/ERROR)",
    )
    args = parser.parse_args(argv_list)

    logging.basicConfig(level=args.log_level.upper())

    # Operator-facing error UX: YAML parse errors, schema-validation
    # errors and capability mismatches are caught here and rendered as
    # ``_LOG.error`` with a non-zero exit code, instead of a Python
    # traceback at the operator (demo-integrity council R5).
    try:
        with args.config.open("r", encoding="utf-8") as fp:
            payload = yaml.safe_load(fp)
        runtime = NodeRuntimeConfig.model_validate(payload)
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
        asyncio.run(_run(runtime))
    except CapabilityMismatchError as exc:
        _LOG.error("rfmesh-node: capability mismatch: %s", exc)
        return 2
    except NotImplementedError as exc:
        _LOG.error("rfmesh-node: %s", exc)
        return 2
    except ValueError as exc:
        _LOG.error("rfmesh-node: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_node_main())
