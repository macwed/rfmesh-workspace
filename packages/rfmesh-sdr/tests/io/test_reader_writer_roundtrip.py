"""WS-A-004 Acceptance 1(a) and 1(b): recorder -> reader roundtrip and sidecar contents."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from rfmesh_sdr.io import IQMetadata, IQReader, IQRecorder
from rfmesh_sdr.io.constants import RTL_SDR_DC_OFFSET, RTL_SDR_SCALE

_SAMPLE_RATE_HZ = 2.048e6
_CENTER_FREQ_HZ = 915e6
_RECORDER_GAIN_DB = 35.0


def _quantize_to_grid(samples: np.ndarray) -> np.ndarray:
    """Reference implementation: quantize complex64 -> uint8 grid -> complex64.

    This is what the on-disk format does to the input by construction;
    comparing against it (rather than against the raw input) lets the
    roundtrip test assert *exact* equality within the format's grid,
    catching off-by-one errors that ``atol=1/127.5`` would mask.
    """
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


def test_record_then_read_is_identity(tmp_path: Path) -> None:
    """Acceptance 1(a): recorder -> reader is identity within the uint8 quantization grid."""
    n_samples = 8192
    # Mix of real and imaginary content covering the full [-1, +1] range so
    # the test exercises both saturation and centre-of-range quantization.
    rng = np.random.default_rng(seed=20260514)
    real = rng.uniform(-0.95, 0.95, size=n_samples).astype(np.float32)
    imag = rng.uniform(-0.95, 0.95, size=n_samples).astype(np.float32)
    original = (real + 1j * imag).astype(np.complex64)
    original_quantized = _quantize_to_grid(original)

    rec = IQRecorder(
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        source="roundtrip_test",
    )
    rec.start(tmp_path / "capture")
    rec.write(original)
    rec.close()

    read_back = IQReader(tmp_path / "capture.iq").read_all()
    assert read_back.shape == (n_samples,)
    assert read_back.dtype == np.complex64
    assert np.allclose(read_back, original_quantized, atol=1.0 / 127.5)
    # The stronger claim: any sample that survived the quantize step
    # should be *exactly* equal on the way out (no rounding drift).
    np.testing.assert_array_equal(read_back, original_quantized)


def test_recorder_metadata_sidecar(tmp_path: Path) -> None:
    """Acceptance 1(b): ``rec.start(path)`` -> ``path.iq`` + ``path.iq.json`` sidecar."""
    n_samples = 2048
    samples = (np.linspace(-0.5, 0.5, n_samples, dtype=np.float32)).astype(np.complex64)

    rec = IQRecorder(
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        source="synthetic",
        serial="dev01",
        gain_db=_RECORDER_GAIN_DB,
        notes="WS-A-004 acceptance",
    )
    rec.start(tmp_path / "capture")
    rec.write(samples)
    rec.close()

    iq_path = tmp_path / "capture.iq"
    sidecar_path = tmp_path / "capture.iq.json"
    assert iq_path.exists()
    assert sidecar_path.exists()

    # Lossless roundtrip of the sidecar via Pydantic.
    parsed = IQMetadata.model_validate_json(sidecar_path.read_text())
    assert parsed.schema_version == "1.0.0"
    assert parsed.format == "rtl_sdr_uint8"
    assert parsed.sample_rate_hz == _SAMPLE_RATE_HZ
    assert parsed.center_freq_hz == _CENTER_FREQ_HZ
    assert parsed.n_samples == n_samples
    assert parsed.source == "synthetic"
    assert parsed.serial == "dev01"
    assert parsed.gain_db == _RECORDER_GAIN_DB
    assert parsed.notes == "WS-A-004 acceptance"
    assert parsed.start_time_utc.tzinfo is not None


def test_recorder_context_manager(tmp_path: Path) -> None:
    """The recorder works as a context manager; close() runs on exit."""
    n_samples = 512
    samples = np.zeros(n_samples, dtype=np.complex64)
    with IQRecorder(
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
    ).start(tmp_path / "ctx") as rec:
        rec.write(samples)
    assert (tmp_path / "ctx.iq").exists()
    assert (tmp_path / "ctx.iq.json").exists()
    parsed = IQMetadata.model_validate_json((tmp_path / "ctx.iq.json").read_text())
    assert parsed.n_samples == n_samples


def test_recorder_accepts_full_iq_path(tmp_path: Path) -> None:
    """A caller passing a fully-qualified ``foo.iq`` writes to that exact path."""
    samples = np.zeros(64, dtype=np.complex64)
    rec = IQRecorder(sample_rate_hz=_SAMPLE_RATE_HZ, center_freq_hz=_CENTER_FREQ_HZ)
    rec.start(tmp_path / "explicit.iq")
    rec.write(samples)
    rec.close()
    assert (tmp_path / "explicit.iq").exists()
    assert (tmp_path / "explicit.iq.json").exists()


def test_recorder_multiple_writes_concatenate(tmp_path: Path) -> None:
    """Two ``write()`` calls of N samples each produce a 2N-sample file."""
    a = (0.25 * np.ones(1024)).astype(np.complex64)
    b = (-0.25 * np.ones(1024)).astype(np.complex64)
    rec = IQRecorder(sample_rate_hz=_SAMPLE_RATE_HZ, center_freq_hz=_CENTER_FREQ_HZ)
    rec.start(tmp_path / "multi")
    rec.write(a)
    rec.write(b)
    rec.close()

    out = IQReader(tmp_path / "multi.iq").read_all()
    assert out.shape == (2048,)
    np.testing.assert_array_equal(out[:1024], _quantize_to_grid(a))
    np.testing.assert_array_equal(out[1024:], _quantize_to_grid(b))
