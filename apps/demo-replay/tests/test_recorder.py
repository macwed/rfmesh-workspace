"""ReplayRecorder unit tests."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from rfmesh_demo_replay import ReplayMetadata, ReplayRecorder
from rfmesh_sdr.simulator import (
    AntennaPattern,
    ArraySpec,
    EmitterSpec,
    SimulationScenario,
    SyntheticReceiver,
)


def _build_single_channel_receiver() -> SyntheticReceiver:
    """One CW emitter, L1 single-channel SyntheticReceiver."""
    scenario = SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=10.0,
                range_m=1000.0,
                frequency_hz=915.0e6,
                tx_power_db=40.0,
            ),
        ),
        antenna=AntennaPattern(hpbw_deg=50.0),
        sample_rate_hz=1.0e5,
        center_freq_hz=915.0e6,
    )
    receiver = SyntheticReceiver(scenario, seed=42)
    receiver.open()
    return receiver


def _build_coherent_receiver() -> SyntheticReceiver:
    """A 2-channel coherent SyntheticReceiver."""
    scenario = SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=10.0,
                range_m=1000.0,
                frequency_hz=915.0e6,
                tx_power_db=40.0,
            ),
        ),
        antenna=AntennaPattern(hpbw_deg=50.0),
        sample_rate_hz=1.0e5,
        center_freq_hz=915.0e6,
        array=ArraySpec.ula(n_elements=2, spacing_m=0.164),
    )
    receiver = SyntheticReceiver(scenario, seed=43)
    receiver.open()
    # Coherent receivers expose ``calibrate``; we do not need a
    # successful one for the recorder test, just an exposed method.
    return receiver


async def test_recorder_writes_iqx_and_sidecar(tmp_path: Path) -> None:
    """Recording one node writes both the .iqx payload and the .json sidecar."""
    receiver = _build_single_channel_receiver()
    try:
        recorder = ReplayRecorder(
            tmp_path,
            scenario_id="rec_test_v1",
            ground_truth_emitter_enu_m=(0.0, 1000.0),
            channel_model={"kind": "free_space"},
        )
        paths = await recorder.record(
            {"smoke": receiver},
            duration_s=0.05,  # 100 kS/s * 0.05 = 5000 samples
            per_node_bearings_deg={"smoke": 10.0},
        )
    finally:
        receiver.close()

    payload = paths["smoke"]
    sidecar = payload.with_suffix(".json")
    assert payload.exists()
    assert sidecar.exists()
    # Sample count from the sidecar must match the .iqx byte count
    # (single-channel complex64 -> 8 bytes per sample).
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    n_samples = sidecar_payload["n_samples"]
    assert n_samples == 5000
    assert payload.stat().st_size == n_samples * 8


async def test_recorder_writes_replay_metadata(tmp_path: Path) -> None:
    """Sidecar carries every ReplayMetadata field the recorder was given."""
    receiver = _build_single_channel_receiver()
    try:
        recorder = ReplayRecorder(
            tmp_path,
            scenario_id="rec_test_v1",
            beat_id="A",
            ground_truth_emitter_enu_m=(1.0, 2.0),
            channel_model={"kind": "free_space", "extra": 7},
        )
        paths = await recorder.record(
            {"alpha": receiver},
            duration_s=0.01,
            per_node_bearings_deg={"alpha": 42.0},
        )
    finally:
        receiver.close()

    sidecar = paths["alpha"].with_suffix(".json")
    meta = ReplayMetadata.model_validate_json(sidecar.read_text(encoding="utf-8"))
    assert meta.scenario_id == "rec_test_v1"
    assert meta.beat_id == "A"
    assert meta.node_id == "alpha"
    assert meta.ground_truth_emitter_enu_m == (1.0, 2.0)
    assert meta.ground_truth_bearing_deg == pytest.approx(42.0)
    assert meta.channel_model == {"kind": "free_space", "extra": 7}
    assert meta.n_channels == 1


async def test_recorder_multichannel_layout(tmp_path: Path) -> None:
    """Multi-channel .iqx interleaves channels within a sample.

    For 2 channels and N samples, the payload size is N * 2 * 8 bytes
    (complex64 = 8 bytes). The first two complex64 values map to
    (s0c0, s0c1).
    """
    receiver = _build_coherent_receiver()
    try:
        recorder = ReplayRecorder(tmp_path, scenario_id="coh_test")
        paths = await recorder.record({"l2": receiver}, duration_s=0.01)
    finally:
        receiver.close()

    payload = paths["l2"]
    sidecar = payload.with_suffix(".json")
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    n_samples = sidecar_payload["n_samples"]
    assert sidecar_payload["n_channels"] == 2
    # Per-sample bytes = n_channels * 8 (complex64).
    assert payload.stat().st_size == n_samples * 2 * 8

    # Sanity: the on-disk layout is reshape-able to (n_samples, n_channels).
    raw = np.fromfile(payload, dtype=np.complex64)
    assert raw.size == n_samples * 2
    interleaved = raw.reshape(n_samples, 2)
    # Both channels should carry real, non-zero IQ for a 40 dB CW emitter.
    assert np.abs(interleaved).mean() > 0.0
