"""Tests for BPSK modulate / demodulate."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rfmesh_dsss.exceptions import DsssError
from rfmesh_dsss.modulation import bpsk_demodulate, bpsk_modulate

_GOLDEN = Path(__file__).parent / "golden" / "bpsk_roundtrip.npz"


def test_roundtrip_exact_for_known_pattern() -> None:
    """``bpsk_demodulate(bpsk_modulate(b)) == b`` for a known mixed pattern."""
    bits = np.array([0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0], dtype=np.int8)
    symbols = bpsk_modulate(bits)
    recovered = bpsk_demodulate(symbols)
    np.testing.assert_array_equal(recovered, bits)


def test_zero_bit_maps_to_plus_one() -> None:
    """Convention: ``0 -> +1 + 0j``."""
    s = bpsk_modulate(np.array([0], dtype=np.int8))
    assert s[0] == complex(1.0, 0.0)


def test_one_bit_maps_to_minus_one() -> None:
    """Convention: ``1 -> -1 + 0j``."""
    s = bpsk_modulate(np.array([1], dtype=np.int8))
    assert s[0] == complex(-1.0, 0.0)


def test_symbols_are_complex64() -> None:
    """Output dtype is ``complex64`` (IQ-block convention from rfmesh-contracts)."""
    s = bpsk_modulate(np.array([0, 1, 0, 1], dtype=np.int8))
    assert s.dtype == np.complex64


def test_demodulate_zero_real_part_maps_to_zero() -> None:
    """Exact-zero boundary is deterministically a zero bit (round-trip stability)."""
    iq = np.array([complex(0.0, 0.0)], dtype=np.complex64)
    bits = bpsk_demodulate(iq)
    assert bits[0] == 0


def test_demodulate_ignores_imaginary_for_bpsk() -> None:
    """Imaginary content does not change BPSK hard decisions (real-axis-only)."""
    iq = np.array(
        [complex(0.5, 100.0), complex(-0.3, -50.0)],
        dtype=np.complex64,
    )
    bits = bpsk_demodulate(iq)
    np.testing.assert_array_equal(bits, np.array([0, 1], dtype=np.int8))


def test_modulate_rejects_invalid_bits() -> None:
    """Non-``{0, 1}`` input raises rather than silently coercing."""
    with pytest.raises(DsssError, match="bits must contain only 0 and 1"):
        bpsk_modulate(np.array([0, 1, 2], dtype=np.int8))


def test_modulate_rejects_multi_dim() -> None:
    """2-D input raises (the contract is 1-D bit-stream)."""
    with pytest.raises(DsssError, match="must be 1-D"):
        bpsk_modulate(np.array([[0, 1], [1, 0]], dtype=np.int8))


def test_demodulate_rejects_multi_dim() -> None:
    """2-D IQ raises (matched-filter output is 1-D in v1.3.0)."""
    with pytest.raises(DsssError, match="must be 1-D"):
        bpsk_demodulate(np.zeros((2, 4), dtype=np.complex64))


def test_golden_match() -> None:
    """Round-trip matches the checked-in golden artefact."""
    npz = np.load(_GOLDEN)
    bits = npz["bits"]
    symbols = bpsk_modulate(bits)
    recovered = bpsk_demodulate(symbols)
    np.testing.assert_array_equal(symbols, npz["symbols"])
    np.testing.assert_array_equal(recovered, npz["recovered"])
