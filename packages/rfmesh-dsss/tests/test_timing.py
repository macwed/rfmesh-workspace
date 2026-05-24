"""Tests for the carrier-phase recovery primitive."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rfmesh_dsss.exceptions import DsssError
from rfmesh_dsss.modulation import bpsk_modulate
from rfmesh_dsss.timing import correct_carrier_phase, estimate_carrier_phase

_GOLDEN = Path(__file__).parent / "golden" / "carrier_phase_recovery.npz"


def test_zero_rotation_recovers_zero_phase() -> None:
    """No rotation in: estimator returns ~0 (mean of squared real symbols is real)."""
    symbols = bpsk_modulate(np.array([0, 1, 0, 1, 0, 1, 0, 1], dtype=np.int8))
    assert estimate_carrier_phase(symbols) == pytest.approx(0.0, abs=1e-9)


def test_30_degree_rotation_recovered() -> None:
    """A +30 deg block rotation is estimated within 0.5 deg of truth."""
    symbols = bpsk_modulate(np.tile(np.array([0, 1], dtype=np.int8), 16))
    rotation_rad = np.pi / 6  # 30 deg
    rotated = symbols * np.exp(np.complex64(1j * rotation_rad))
    recovered = estimate_carrier_phase(rotated)
    assert recovered == pytest.approx(rotation_rad, abs=np.deg2rad(0.5))


def test_correct_recovers_real_axis_alignment() -> None:
    """After de-rotation, BPSK symbols sit on (or near) the real axis."""
    symbols = bpsk_modulate(np.tile(np.array([0, 1], dtype=np.int8), 16))
    rotation_rad = np.pi / 5  # 36 deg, off the cardinal points
    rotated = (symbols * np.exp(np.complex64(1j * rotation_rad))).astype(np.complex64)
    derotated = correct_carrier_phase(rotated)
    # Imaginary part should be near zero (up to the 180-deg ambiguity
    # documented in the timing module: the recovery may produce a
    # wholesale +/- flip vs the input, which the framing-layer CRC
    # catches downstream). Check via |Im| / |Re| ratio per symbol.
    real_part = derotated.real
    imag_part = derotated.imag
    abs_ratios = np.abs(imag_part) / np.maximum(np.abs(real_part), 1e-6)
    assert abs_ratios.max() < 1e-3


def test_no_signal_returns_zero_phase() -> None:
    """All-zero / very-small-magnitude input: estimator returns 0 (no de-rotation)."""
    symbols = np.full(16, 1e-20 + 0j, dtype=np.complex64)
    assert estimate_carrier_phase(symbols) == 0.0


def test_correct_passes_through_when_no_signal() -> None:
    """``correct_carrier_phase`` with no-signal input returns a copy of the input."""
    symbols = np.full(16, 1e-20 + 0j, dtype=np.complex64)
    out = correct_carrier_phase(symbols)
    assert out is not symbols  # is a copy
    np.testing.assert_array_equal(out, symbols)


def test_estimate_rejects_empty_or_multidim() -> None:
    """Bad shape input raises rather than silently returning 0."""
    with pytest.raises(DsssError, match="must be 1-D"):
        estimate_carrier_phase(np.zeros((2, 4), dtype=np.complex64))
    with pytest.raises(DsssError, match="non-empty"):
        estimate_carrier_phase(np.zeros(0, dtype=np.complex64))


def test_golden_match() -> None:
    """Freshly-computed rotation + recovery matches the checked-in artefact."""
    npz = np.load(_GOLDEN)
    symbols = bpsk_modulate(npz["bits"])
    rotation_rad = float(npz["rotation_rad"])
    rotated = (symbols * np.exp(np.complex64(1j * rotation_rad))).astype(np.complex64)
    derotated = correct_carrier_phase(rotated)
    np.testing.assert_allclose(rotated, npz["rotated"], atol=1e-6)
    np.testing.assert_allclose(derotated, npz["derotated"], atol=1e-6)
