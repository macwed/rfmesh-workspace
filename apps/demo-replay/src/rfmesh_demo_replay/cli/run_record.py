"""``rfmesh-demo-record`` CLI -- capture per-node IQ to ``.iqx + .json`` files.

Usage::

    rfmesh-demo-record --scenario scenarios/<name>.yaml \
                       --output recordings/<session>/ \
                       --duration-s 60

For v1.0 this drives the simulator (``SyntheticReceiver``) only; the
hardware path arrives when ``rfmesh-sdr`` ships its native Receiver
implementations. The CLI builds one ``SyntheticReceiver`` per node
in the scenario (using the same geometry the replay orchestrator
uses), opens / configures / optionally calibrates each, and writes
out the per-node ``.iqx`` + ``.json`` pair under ``--output``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

from rfmesh_sdr.simulator import (  # type: ignore[import-untyped]
    AntennaPattern,
    ArraySpec,
    SimulationScenario,
    SyntheticReceiver,
)
from rfmesh_sdr.simulator import (
    EmitterSpec as SimEmitterSpec,
)

from ..recorder import ReplayRecorder
from ..replay import _reify_channel_model
from ..scenario import Scenario, ScenarioLoader

if TYPE_CHECKING:
    from rfmesh_contracts import Receiver

_LOG = logging.getLogger(__name__)

_DEFAULT_HALF_WAVELENGTH_M_915MHZ: float = 0.164


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rfmesh-demo-record")
    parser.add_argument(
        "--scenario",
        type=Path,
        required=True,
        help="Path to a scenario YAML file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for the recording session.",
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=10.0,
        help="Per-node capture duration in seconds. Default 10.0.",
    )
    parser.add_argument(
        "--beat-id",
        type=str,
        default=None,
        help="Optional beat id stamped onto every sidecar.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="logging level (DEBUG/INFO/WARNING/ERROR).",
    )
    return parser


def _build_receiver(scenario: Scenario, node_id: str) -> tuple[Receiver, float, float]:
    """Build a SyntheticReceiver for one node + return (bearing, range_m)."""
    node_spec = scenario.node_by_id(node_id)
    node_e, node_n = node_spec.enu_position_m
    em_e, em_n = scenario.emitter.enu_position_m
    de = em_e - node_e
    dn = em_n - node_n
    range_m = math.hypot(de, dn)
    bearing_deg = math.degrees(math.atan2(de, dn)) % 360.0

    emitter = SimEmitterSpec(
        azimuth_deg=bearing_deg,
        range_m=range_m,
        frequency_hz=scenario.emitter.center_freq_hz,
        tx_power_db=scenario.emitter.emit_power_db_above_noise,
    )
    array_sim: ArraySpec | None = None
    if node_spec.array is not None:
        array_sim = ArraySpec.ula(
            n_elements=node_spec.array.n_elements,
            spacing_m=node_spec.array.element_spacing_m or _DEFAULT_HALF_WAVELENGTH_M_915MHZ,
        )
    sim_scenario = SimulationScenario(
        emitters=(emitter,),
        antenna=AntennaPattern(hpbw_deg=50.0),
        sample_rate_hz=node_spec.sdr.sample_rate_hz,
        center_freq_hz=node_spec.sdr.center_freq_hz,
        channel=_reify_channel_model(scenario.channel_model),
        array=array_sim,
    )
    receiver = SyntheticReceiver(sim_scenario, seed=hash(node_id) & 0xFFFFFFFF)
    receiver.open()
    node_cfg = node_spec.to_node_config(scenario.enu_origin)
    receiver.configure(node_cfg)
    if hasattr(receiver, "calibrate"):
        try:
            receiver.calibrate()
        except Exception as exc:
            _LOG.warning("calibration failed for %s: %s", node_id, exc)
    return receiver, bearing_deg, range_m


async def _run_async(args: argparse.Namespace) -> int:
    scenario = ScenarioLoader().load(args.scenario)
    receivers: dict[str, Receiver] = {}
    bearings: dict[str, float] = {}
    try:
        for node_spec in scenario.nodes:
            receiver, bearing_deg, _range_m = _build_receiver(scenario, node_spec.node_id)
            receivers[node_spec.node_id] = receiver
            bearings[node_spec.node_id] = bearing_deg

        recorder = ReplayRecorder(
            args.output,
            scenario_id=scenario.scenario_id,
            beat_id=args.beat_id,
            ground_truth_emitter_enu_m=scenario.emitter.enu_position_m,
            channel_model=scenario.channel_model.to_dict(),
        )
        paths = await recorder.record(
            receivers,
            args.duration_s,
            per_node_bearings_deg=bearings,
        )
        for node_id, path in paths.items():
            _LOG.info("recorded %s -> %s", node_id, path)
    finally:
        for receiver in receivers.values():
            try:
                receiver.close()
            except Exception as exc:
                _LOG.warning("receiver.close failed: %s", exc)
    return 0


def run_record_main(argv: list[str] | None = None) -> int:
    """``rfmesh-demo-record`` entry point -- returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        _LOG.error("rfmesh-demo-record: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_record_main())
