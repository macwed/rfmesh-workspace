"""WS-A-004 Acceptance 1(h): end-to-end record + replay through ``SyntheticReceiver``.

The honesty check at the I/O layer: what comes out of the
``SyntheticReceiver``, recorded to disk and read back, equals what
came out of the receiver -- modulo the uint8 quantization grid. If
this test fails, either the recorder is reordering / dropping
samples, or the reader is reading them back wrong; either way the
golden-file regression discipline WS-B will rely on becomes
unrooted.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from rfmesh_sdr import SimulationScenario, SyntheticReceiver
from rfmesh_sdr.io import IQReader, IQRecorder
from rfmesh_sdr.io.constants import RTL_SDR_DC_OFFSET, RTL_SDR_SCALE

_N_SAMPLES = 8192
_SAMPLE_RATE_HZ = 2.048e6
_CENTER_FREQ_HZ = 915e6


def _quantize_to_grid(samples: np.ndarray) -> np.ndarray:
    """Mirror the on-disk uint8 quantization (see test_reader_writer_roundtrip)."""
    real_bytes = np.clip(
        np.round(samples.real * RTL_SDR_SCALE + RTL_SDR_DC_OFFSET),
        0.0,
        255.0,
    ).astype(np.uint8)
    imag_bytes = np.clip(
        np.round(samples.imag * RTL_SDR_SCALE + RTL_SDR_DC_OFFSET),
        0.0,
        255.0,
    ).astype(np.uint8)
    real_back = (real_bytes.astype(np.float32) - RTL_SDR_DC_OFFSET) / RTL_SDR_SCALE
    imag_back = (imag_bytes.astype(np.float32) - RTL_SDR_DC_OFFSET) / RTL_SDR_SCALE
    return (real_back + 1j * imag_back).astype(np.complex64)


def test_record_synthetic_receiver_replay(
    default_scenario: SimulationScenario,
    tmp_path: Path,
) -> None:
    """End-to-end: SyntheticReceiver -> IQRecorder -> IQReader matches the source."""
    receiver = SyntheticReceiver(default_scenario, seed=42)
    receiver.open()
    produced = receiver.read(_N_SAMPLES)
    receiver.close()

    # Sanity on the producer side -- guards against the test masking a
    # SyntheticReceiver regression as an I/O bug.
    assert produced.shape == (_N_SAMPLES,)
    assert produced.dtype == np.complex64

    # ``default_scenario.sample_rate_hz`` is 2_048_000.0 by construction
    # of the conftest fixture; the recorder is happy with that value and
    # stamps it on the sidecar.
    rec = IQRecorder(
        sample_rate_hz=default_scenario.sample_rate_hz,
        center_freq_hz=default_scenario.center_freq_hz,
        source="synthetic_receiver",
    )
    rec.start(tmp_path / "replay")
    rec.write(produced)
    rec.close()

    reader = IQReader(tmp_path / "replay.iq")
    read_back = reader.read_all()
    # The strong invariant: byte-exact match against the quantized source.
    np.testing.assert_array_equal(read_back, _quantize_to_grid(produced))
    # And the loose invariant the acceptance text demands.
    assert np.allclose(read_back, _quantize_to_grid(produced), atol=1.0 / 127.5)

    # Sidecar pins the producer parameters -- this is the provenance the
    # golden-file regression tests will rely on.
    assert reader.metadata is not None
    assert reader.metadata.sample_rate_hz == default_scenario.sample_rate_hz
    assert reader.metadata.center_freq_hz == default_scenario.center_freq_hz
    assert reader.metadata.n_samples == _N_SAMPLES
    assert reader.metadata.source == "synthetic_receiver"
