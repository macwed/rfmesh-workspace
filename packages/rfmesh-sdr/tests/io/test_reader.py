"""WS-A-004 Acceptance 1(c) and 1(d): ``IQReader`` chunked reads and bad-path handling."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from rfmesh_sdr.exceptions import MalformedIQFileError
from rfmesh_sdr.io import IQReader, IQRecorder

_SAMPLE_RATE_HZ = 2.048e6
_CENTER_FREQ_HZ = 915e6

# Chunking test (test_iq_reader_chunked_read): 10000 samples in chunks of 3333
# produces three full chunks of 3333 plus a short trailing chunk of 1.
_CHUNKED_TOTAL_SAMPLES = 10_000
_CHUNKED_CHUNK_SIZE = 3_333
# 10000 / 3333 = 3 full chunks of 3333 + 1 short trailing chunk of 1.
_CHUNKED_EXPECTED_NUM_CHUNKS = 4
_CHUNKED_FINAL_CHUNK_SIZE = 1

# Smaller fixed-size captures used by the remaining read-back tests.
_SIDECAR_TEST_N_SAMPLES = 2048
_TRUNCATED_HALF_N_SAMPLES = 2048
_CONTEXT_TEST_N_SAMPLES = 4096
_NO_SIDECAR_N_SAMPLES = 1024
_TEST_GAIN_DB = 35.0  # placeholder, not asserted in this module
_NOW_UTC_TEST_N_SAMPLES = 64


def _record_known_samples(path: Path, n_samples: int) -> np.ndarray:
    """Record a deterministic complex64 stream to ``<path>.iq`` and return what was written.

    Helper for the reader tests: gives them a real file produced by the
    real recorder, so we exercise the on-disk format the way a downstream
    consumer would.
    """
    # Deterministic pseudo-IQ -- a slow sweep across the unit disk so each
    # sample maps to a *different* uint8 pair (catches off-by-one in
    # chunking that a constant signal would not).
    t = np.arange(n_samples, dtype=np.float32) / n_samples
    angle = 2.0 * np.pi * 5.0 * t
    samples = (0.5 * np.cos(angle) + 1j * 0.5 * np.sin(angle)).astype(np.complex64)

    rec = IQRecorder(
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        source="test_reader_fixture",
    )
    rec.start(path)
    rec.write(samples)
    rec.close()
    return samples


def test_iq_reader_chunked_read(tmp_path: Path) -> None:
    """Concatenating ``iter_chunks(k)`` equals ``read_all()``; final chunk may be short."""
    _record_known_samples(tmp_path / "capture", _CHUNKED_TOTAL_SAMPLES)
    iq_path = tmp_path / "capture.iq"

    reader = IQReader(iq_path)
    bulk = reader.read_all()
    assert bulk.shape == (_CHUNKED_TOTAL_SAMPLES,)
    assert bulk.dtype == np.complex64

    # Streaming read: chunk size deliberately not a divisor of n_samples
    # so the loop produces a short final chunk -- the test's whole point.
    chunks: list[np.ndarray] = list(IQReader(iq_path).iter_chunks(_CHUNKED_CHUNK_SIZE))

    # First three chunks are full-size, last is short.
    assert len(chunks) == _CHUNKED_EXPECTED_NUM_CHUNKS
    assert chunks[0].shape == (_CHUNKED_CHUNK_SIZE,)
    assert chunks[1].shape == (_CHUNKED_CHUNK_SIZE,)
    assert chunks[2].shape == (_CHUNKED_CHUNK_SIZE,)
    assert chunks[3].shape == (_CHUNKED_FINAL_CHUNK_SIZE,)

    rejoined = np.concatenate(chunks)
    assert rejoined.shape == (_CHUNKED_TOTAL_SAMPLES,)
    np.testing.assert_array_equal(rejoined, bulk)


def test_iter_chunks_rejects_non_positive(tmp_path: Path) -> None:
    """``chunk_samples <= 0`` is a programming error, surfaced loudly."""
    _record_known_samples(tmp_path / "capture", _NO_SIDECAR_N_SAMPLES)
    reader = IQReader(tmp_path / "capture.iq")
    with pytest.raises(ValueError, match="chunk_samples must be > 0"):
        list(reader.iter_chunks(0))
    with pytest.raises(ValueError, match="chunk_samples must be > 0"):
        list(reader.iter_chunks(-1))


def test_iq_reader_bad_path_raises(tmp_path: Path) -> None:
    """Missing file -> FileNotFoundError; odd byte count -> MalformedIQFileError."""
    missing = tmp_path / "does_not_exist.iq"
    with pytest.raises(FileNotFoundError):
        IQReader(missing)

    truncated = tmp_path / "truncated.iq"
    # 7 bytes is not a multiple of 2 -- a half-sample at the end is the
    # truncation symptom we want surfaced.
    truncated.write_bytes(b"\x00\x01\x02\x03\x04\x05\x06")
    with pytest.raises(MalformedIQFileError, match="not a multiple of 2"):
        IQReader(truncated)


def test_iq_reader_metadata_loaded_when_sidecar_present(tmp_path: Path) -> None:
    """When ``<path>.iq.json`` exists, ``reader.metadata`` is the parsed IQMetadata."""
    _record_known_samples(tmp_path / "capture", _SIDECAR_TEST_N_SAMPLES)
    reader = IQReader(tmp_path / "capture.iq")
    assert reader.metadata is not None
    assert reader.metadata.sample_rate_hz == _SAMPLE_RATE_HZ
    assert reader.metadata.center_freq_hz == _CENTER_FREQ_HZ
    assert reader.metadata.n_samples == _SIDECAR_TEST_N_SAMPLES
    assert reader.metadata.source == "test_reader_fixture"


def test_iq_reader_metadata_is_none_when_sidecar_missing(tmp_path: Path) -> None:
    """A capture with no sidecar reads back fine; ``metadata`` is ``None``."""
    samples = (np.linspace(-0.5, 0.5, _NO_SIDECAR_N_SAMPLES).astype(np.float32)).astype(
        np.complex64,
    )
    rec = IQRecorder(sample_rate_hz=_SAMPLE_RATE_HZ, center_freq_hz=_CENTER_FREQ_HZ)
    rec.start(tmp_path / "no_sidecar")
    rec.write(samples)
    rec.close()
    # Delete the sidecar to simulate a legacy capture.
    sidecar = tmp_path / "no_sidecar.iq.json"
    assert sidecar.exists()
    sidecar.unlink()

    reader = IQReader(tmp_path / "no_sidecar.iq")
    assert reader.metadata is None
    out = reader.read_all()
    assert out.shape == (_NO_SIDECAR_N_SAMPLES,)


def test_iq_reader_truncated_payload_vs_sidecar(tmp_path: Path) -> None:
    """If the sidecar claims more samples than the payload contains, raise."""
    _record_known_samples(tmp_path / "capture", _CONTEXT_TEST_N_SAMPLES)
    iq_path = tmp_path / "capture.iq"
    sidecar_path = tmp_path / "capture.iq.json"

    # Truncate the payload mid-write while keeping the sidecar.
    iq_path.write_bytes(iq_path.read_bytes()[: _TRUNCATED_HALF_N_SAMPLES * 2])

    with pytest.raises(MalformedIQFileError, match="truncated"):
        IQReader(iq_path)

    # Sanity: deleting the sidecar should remove the mismatch -- reader OK.
    sidecar_path.unlink()
    reader = IQReader(iq_path)
    assert reader.metadata is None
    assert reader.n_samples == _TRUNCATED_HALF_N_SAMPLES


def test_iq_reader_malformed_sidecar_raises(tmp_path: Path) -> None:
    """An unparseable sidecar must be loud, not silently ignored."""
    _record_known_samples(tmp_path / "capture", _NO_SIDECAR_N_SAMPLES)
    sidecar_path = tmp_path / "capture.iq.json"
    sidecar_path.write_text("{ this is not valid json")
    with pytest.raises(MalformedIQFileError):
        IQReader(tmp_path / "capture.iq")


def test_iq_reader_wrong_schema_version_in_sidecar_raises(tmp_path: Path) -> None:
    """A sidecar with a non-1.0.0 schema_version fails validation."""
    _record_known_samples(tmp_path / "capture", _NO_SIDECAR_N_SAMPLES)
    sidecar_path = tmp_path / "capture.iq.json"
    bad = (
        '{"schema_version": "0.9.0", "sample_rate_hz": 2048000.0, '
        '"center_freq_hz": 915000000.0, "n_samples": 1024, '
        '"start_time_utc": "2026-05-14T10:30:00+00:00", "format": "rtl_sdr_uint8"}'
    )
    sidecar_path.write_text(bad)
    with pytest.raises(MalformedIQFileError):
        IQReader(tmp_path / "capture.iq")


def test_iq_reader_context_manager(tmp_path: Path) -> None:
    """The reader supports the ``with`` ergonomics for streaming reads."""
    _record_known_samples(tmp_path / "capture", _CONTEXT_TEST_N_SAMPLES)
    with IQReader(tmp_path / "capture.iq") as reader:
        chunks = list(reader.iter_chunks(_NO_SIDECAR_N_SAMPLES))
    assert sum(c.size for c in chunks) == _CONTEXT_TEST_N_SAMPLES


def test_iq_reader_read_all_returns_empty_on_empty_payload(tmp_path: Path) -> None:
    """An empty payload (0 samples) reads back as an empty complex64 array.

    Empty payloads are corner cases -- a recorder that closed before any
    write produced one -- and the reader must still operate on them
    sensibly. The sidecar-mismatch test above ensures we don't pair
    an empty payload with a sidecar that claims samples; here we just
    check the empty case in isolation.
    """
    iq_path = tmp_path / "empty.iq"
    iq_path.write_bytes(b"")
    # No sidecar paired with the empty payload (an n_samples=0 sidecar
    # would itself fail the metadata validator's gt-zero constraint).
    reader = IQReader(iq_path)
    assert reader.n_samples == 0
    out = reader.read_all()
    assert out.shape == (0,)
    assert out.dtype == np.complex64

    # iter_chunks on empty file yields nothing.
    assert list(reader.iter_chunks(_NOW_UTC_TEST_N_SAMPLES)) == []


def test_iq_metadata_construction_uses_now_utc(tmp_path: Path) -> None:
    """Sanity: a recorder-written sidecar carries a tz-aware UTC datetime.

    Surfaced as a separate small test to make the producer-side invariant
    explicit: the *recorder* is the one that calls ``datetime.now(UTC)``,
    so any future change to it must keep the result tz-aware.
    """
    before = datetime.now(UTC)
    _record_known_samples(tmp_path / "capture", _NOW_UTC_TEST_N_SAMPLES)
    after = datetime.now(UTC)
    md = IQReader(tmp_path / "capture.iq").metadata
    assert md is not None
    assert md.start_time_utc.tzinfo is not None
    assert before <= md.start_time_utc <= after
