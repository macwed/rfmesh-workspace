"""Tests for ``LoopbackChannel`` and ``LoopbackReceiver`` (ADR-025 Iter 3)."""

from __future__ import annotations

import numpy as np
import pytest
from rfmesh_contracts import Receiver
from rfmesh_sdr.exceptions import InvalidReadSizeError, ReceiverNotOpenError
from rfmesh_sdr.simulator import LoopbackChannel, LoopbackReceiver


def test_lossless_roundtrip() -> None:
    """Without AWGN or multipath, write then read returns the same chips."""
    channel = LoopbackChannel(sample_rate_hz=1e6)
    iq = (np.arange(16, dtype=np.float32) + 1j * np.arange(16, dtype=np.float32)).astype(
        np.complex64,
    )
    channel.write(iq)
    out = channel.read(iq.size)
    np.testing.assert_array_equal(out, iq)


def test_buffer_available_tracks_writes_and_reads() -> None:
    """``available()`` matches write minus consume."""
    channel = LoopbackChannel(sample_rate_hz=1e6)
    channel.write(np.ones(100, dtype=np.complex64))
    assert channel.available() == 100
    channel.read(40)
    assert channel.available() == 60
    channel.read(60)
    assert channel.available() == 0


def test_partial_read_raises_buffer_error() -> None:
    """Reading more than was written raises (B3 -- no silent short read)."""
    channel = LoopbackChannel(sample_rate_hz=1e6)
    channel.write(np.ones(8, dtype=np.complex64))
    with pytest.raises(BufferError, match="only 8 available"):
        channel.read(9)


def test_zero_read_raises() -> None:
    """``read(0)`` is a programming bug."""
    channel = LoopbackChannel(sample_rate_hz=1e6)
    channel.write(np.ones(4, dtype=np.complex64))
    with pytest.raises(ValueError, match="n_samples"):
        channel.read(0)


def test_awgn_perturbs_buffer() -> None:
    """Configured chip_snr_db adds non-trivial AWGN to written samples."""
    channel = LoopbackChannel(sample_rate_hz=1e6, chip_snr_db=-10.0, rng_seed=42)
    iq = np.ones(1024, dtype=np.complex64)  # constant +1
    channel.write(iq)
    out = channel.read(iq.size)
    # Output deviates from the input by chip-level noise. Expected
    # noise sigma per complex sample = sqrt(1 / (2 * 0.1)) ~= 2.24 in
    # each component. Power difference should be measurable: in fact
    # noise variance >> signal variance at this SNR.
    diff = out - iq
    measured_sigma = np.std(diff.real) + np.std(diff.imag)  # ~4.48
    assert measured_sigma > 1.0  # very tolerant -- this is "non-trivial"
    assert not np.array_equal(out, iq)


def test_awgn_seed_deterministic() -> None:
    """Two channels with the same seed produce byte-identical AWGN."""
    iq = np.ones(64, dtype=np.complex64)
    ch1 = LoopbackChannel(sample_rate_hz=1e6, chip_snr_db=-5.0, rng_seed=2026)
    ch2 = LoopbackChannel(sample_rate_hz=1e6, chip_snr_db=-5.0, rng_seed=2026)
    ch1.write(iq)
    ch2.write(iq)
    np.testing.assert_array_equal(ch1.read(64), ch2.read(64))


def test_multipath_fir_applied() -> None:
    """Non-identity taps convolve the input on write (single-path = identity)."""
    # Two-tap FIR: y[n] = x[n] + 0.5 * x[n-1] (causal-like in 'same' mode
    # the second tap acts as the centred neighbour).
    channel = LoopbackChannel(
        sample_rate_hz=1e6,
        multipath_taps=(1.0 + 0j, 0.5 + 0j),
    )
    impulse = np.zeros(8, dtype=np.complex64)
    impulse[3] = 1.0
    channel.write(impulse)
    out = channel.read(8)
    # numpy.convolve(mode='same') with len(x)=8, len(taps)=2 trims
    # symmetrically; we only assert the impulse spreads across two
    # samples rather than staying isolated at index 3.
    nonzero = np.argwhere(np.abs(out) > 1e-6).flatten().tolist()
    assert len(nonzero) >= 2


def test_identity_multipath_is_passthrough() -> None:
    """Default ``(1+0j,)`` taps must not perturb the input."""
    channel = LoopbackChannel(sample_rate_hz=1e6)
    iq = (np.random.default_rng(1).standard_normal(32)
          + 1j * np.random.default_rng(2).standard_normal(32)).astype(np.complex64)
    channel.write(iq)
    np.testing.assert_array_equal(channel.read(iq.size), iq)


def test_reset_clears_buffer() -> None:
    """``reset()`` drops every buffered sample."""
    channel = LoopbackChannel(sample_rate_hz=1e6)
    channel.write(np.ones(50, dtype=np.complex64))
    assert channel.available() == 50
    channel.reset()
    assert channel.available() == 0


def test_constructor_validates_sample_rate() -> None:
    """Non-positive sample rate is a config bug."""
    with pytest.raises(ValueError, match="sample_rate_hz"):
        LoopbackChannel(sample_rate_hz=0.0)


def test_constructor_validates_taps_nonempty() -> None:
    """Empty multipath taps would silently swallow every chip."""
    with pytest.raises(ValueError, match="non-empty"):
        LoopbackChannel(sample_rate_hz=1e6, multipath_taps=())


# ----------------------------------------------------------------------
# LoopbackReceiver
# ----------------------------------------------------------------------


def test_receiver_satisfies_protocol() -> None:
    """``LoopbackReceiver`` structurally satisfies the Receiver Protocol."""
    rx = LoopbackReceiver(LoopbackChannel(sample_rate_hz=1e6))
    assert isinstance(rx, Receiver)


def test_receiver_read_before_open_raises() -> None:
    """Lifecycle: read before open raises (B3)."""
    rx = LoopbackReceiver(LoopbackChannel(sample_rate_hz=1e6))
    with pytest.raises(ReceiverNotOpenError):
        rx.read(4)


def test_receiver_read_after_close_raises() -> None:
    """Close invalidates read authority."""
    channel = LoopbackChannel(sample_rate_hz=1e6)
    channel.write(np.ones(4, dtype=np.complex64))
    rx = LoopbackReceiver(channel)
    rx.open()
    rx.read(2)
    rx.close()
    with pytest.raises(ReceiverNotOpenError):
        rx.read(2)


def test_receiver_zero_read_raises() -> None:
    rx = LoopbackReceiver(LoopbackChannel(sample_rate_hz=1e6))
    rx.open()
    with pytest.raises(InvalidReadSizeError):
        rx.read(0)


def test_receiver_capabilities_match_channel() -> None:
    """``capabilities()`` reflects the channel's sample rate; driver = ``loopback``."""
    channel = LoopbackChannel(sample_rate_hz=2_000_000.0)
    rx = LoopbackReceiver(channel)
    caps = rx.capabilities()
    assert caps.driver == "loopback"
    assert caps.n_coherent_channels == 1
    assert caps.actual_sample_rate_hz == 2_000_000.0
    assert caps.is_power_calibrated is False


def test_receiver_reads_what_transmitter_wrote() -> None:
    """End-of-day: write side then read side returns the same samples."""
    channel = LoopbackChannel(sample_rate_hz=1e6)
    iq = (np.arange(32, dtype=np.float32) + 1j * np.arange(32, dtype=np.float32)).astype(
        np.complex64,
    )
    channel.write(iq)
    rx = LoopbackReceiver(channel)
    rx.open()
    out = rx.read(iq.size)
    np.testing.assert_array_equal(out, iq)
