"""Tests for matched-filter correlation + preamble acquisition."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rfmesh_dsss.correlation import (
    DEFAULT_AMBIGUITY_RATIO,
    DEFAULT_THRESHOLD_RATIO,
    acquire_preamble,
    matched_filter,
)
from rfmesh_dsss.exceptions import AcquisitionFailedError
from rfmesh_dsss.modulation import bpsk_modulate
from rfmesh_dsss.pn_sequence import generate_m_sequence
from rfmesh_dsss.spreading import spread

_GOLDEN = Path(__file__).parent / "golden" / "correlation_peak.npz"


def _make_preamble_chips(pn_seed: int = 1) -> np.ndarray:
    """Synthesise the canonical 64-bit preamble pre-spread by the PN sequence.

    Uses ``framing._preamble_bits`` (length-63 m-sequence + 1 pad)
    rather than constructing the bit pattern by hand here -- the
    framing module owns the preamble convention; this helper just
    spreads it.
    """
    from rfmesh_dsss.framing import _preamble_bits

    pn = generate_m_sequence(10, (10, 3), seed=pn_seed)
    preamble_bits = _preamble_bits()
    preamble_symbols = bpsk_modulate(preamble_bits)
    return spread(preamble_symbols, pn)


def test_matched_filter_clean_peak_index() -> None:
    """A noise-free embedded preamble: peak at the correct convolution index."""
    preamble = _make_preamble_chips()
    received = np.zeros(preamble.size + 4096, dtype=np.complex64)
    embed_offset = 2048
    received[embed_offset : embed_offset + preamble.size] = preamble
    corr = matched_filter(received, preamble)
    peak_index = int(np.argmax(corr))
    # The full-correlation peak appears at offset + (replica_size - 1).
    assert peak_index == embed_offset + preamble.size - 1


def test_matched_filter_output_length() -> None:
    """``len(corr) == len(received) + len(replica) - 1`` (linear convolution)."""
    preamble = _make_preamble_chips()
    received = np.zeros(preamble.size + 100, dtype=np.complex64)
    corr = matched_filter(received, preamble)
    assert corr.size == received.size + preamble.size - 1


def test_matched_filter_rejects_short_received() -> None:
    """``received`` shorter than ``replica`` raises."""
    preamble = _make_preamble_chips()
    short = np.zeros(preamble.size - 10, dtype=np.complex64)
    with pytest.raises(AcquisitionFailedError, match="at least as long"):
        matched_filter(short, preamble)


def test_acquire_preamble_returns_chip_index() -> None:
    """``acquire_preamble`` recovers the embed offset on noise-free input."""
    preamble = _make_preamble_chips()
    received = np.zeros(preamble.size + 4096, dtype=np.complex64)
    embed_offset = 2048
    received[embed_offset : embed_offset + preamble.size] = preamble
    chip_index = acquire_preamble(received, preamble)
    assert chip_index == embed_offset


def test_acquire_preamble_under_awgn_low_snr() -> None:
    """Acquisition succeeds at channel SNR around 0 dB after spreading.

    The preamble carries ``2 * 64 * 1023 = 130944`` chips of energy
    (BPSK symbols at amplitude 1, length-64 preamble pre-spread by
    1023-chip PN). Channel SNR of 0 dB at the chip level still leaves
    a coherent processing-gain headroom in the correlator. This test
    runs five trials at SNR ~0 dB and verifies acquisition recovers
    the embed offset on every trial -- a regression anchor against
    a future correlator change that loses the gain.
    """
    rng = np.random.default_rng(20260524)
    preamble = _make_preamble_chips()
    embed_offset = 4096
    n_trials = 5
    for _ in range(n_trials):
        received = np.zeros(preamble.size + 8192, dtype=np.complex64)
        received[embed_offset : embed_offset + preamble.size] = preamble
        # Add complex AWGN at chip SNR ~0 dB.
        noise = (
            rng.standard_normal(received.size) + 1j * rng.standard_normal(received.size)
        ) * 0.7  # 0.7 ~= 1 / sqrt(2) keeps total complex noise variance ~1
        received = (received + noise).astype(np.complex64)
        chip_index = acquire_preamble(received, preamble)
        # Allow a one-chip slop on the recovered offset; the matched
        # filter under noise can shift by up to a few chips and still
        # be honest about the start of frame.
        assert abs(chip_index - embed_offset) <= 1


def test_acquire_preamble_pure_noise_refuses() -> None:
    """Pure complex-Gaussian noise (no preamble): acquisition refuses."""
    rng = np.random.default_rng(202605240)
    preamble = _make_preamble_chips()
    received = (
        rng.standard_normal(preamble.size + 4096)
        + 1j * rng.standard_normal(preamble.size + 4096)
    ).astype(np.complex64)
    with pytest.raises(AcquisitionFailedError, match="no peak above threshold"):
        acquire_preamble(received, preamble)


def test_acquire_preamble_ambiguous_peaks_refuses() -> None:
    """Two equally-strong embedded preambles: acquisition refuses with 'ambiguous'.

    Embeds the preambles with a separation greater than ``replica.size``
    (the guard band the ambiguity check excludes around the main peak).
    Both preambles produce real peaks at the convolution output, and
    the second is outside the guard band -- so the runner-up ratio
    is ~1.0 and ``acquire_preamble`` raises with reason
    ``"ambiguous peaks"`` (the operator-visible cue for multipath
    or a second active transmitter on the same code).
    """
    preamble = _make_preamble_chips()
    # Separation = 2 * preamble.size = 130944 chips. Buffer must
    # fit both embeds + post-embed slack so the matched-filter
    # output covers both peaks honestly.
    separation = 2 * preamble.size
    buffer_size = separation + preamble.size + 4096
    received = np.zeros(buffer_size, dtype=np.complex64)
    embed1 = 1024
    embed2 = embed1 + separation
    received[embed1 : embed1 + preamble.size] = preamble
    received[embed2 : embed2 + preamble.size] = preamble
    with pytest.raises(AcquisitionFailedError, match="ambiguous peaks"):
        acquire_preamble(received, preamble)


def test_acquire_preamble_bad_threshold_raises() -> None:
    """Negative / zero threshold is a configuration error, not silent."""
    preamble = _make_preamble_chips()
    received = np.zeros(preamble.size + 100, dtype=np.complex64)
    received[:preamble.size] = preamble
    with pytest.raises(AcquisitionFailedError, match="threshold_ratio"):
        acquire_preamble(received, preamble, threshold_ratio=0.0)


def test_default_constants_are_positive() -> None:
    """The shipped defaults are physically plausible (not zero / negative)."""
    assert DEFAULT_THRESHOLD_RATIO > 1.0
    assert 0.0 < DEFAULT_AMBIGUITY_RATIO < 1.0


def test_golden_match() -> None:
    """Freshly-computed correlation matches the checked-in golden artefact."""
    npz = np.load(_GOLDEN)
    preamble = _make_preamble_chips()
    embed_offset = int(npz["embed_offset"])
    buffer_size = preamble.size + 8192
    received = np.zeros(buffer_size, dtype=np.complex64)
    received[embed_offset : embed_offset + preamble.size] = preamble
    corr = matched_filter(received, preamble)
    assert int(np.argmax(corr)) == int(npz["peak_index"])
    assert float(corr[int(np.argmax(corr))]) == pytest.approx(
        float(npz["peak_value"]), rel=1e-6,
    )
    assert int(npz["preamble_chip_count"]) == preamble.size
