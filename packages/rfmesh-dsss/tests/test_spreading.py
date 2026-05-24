"""Tests for the spread / despread chip-level primitives."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rfmesh_dsss.exceptions import DsssError
from rfmesh_dsss.modulation import bpsk_demodulate, bpsk_modulate
from rfmesh_dsss.pn_sequence import generate_m_sequence
from rfmesh_dsss.spreading import despread, spread

_GOLDEN = Path(__file__).parent / "golden" / "spread_despread_clean.npz"


def test_chip_count_is_symbols_times_pn() -> None:
    """``spread`` emits exactly ``M * N`` chips."""
    pn = generate_m_sequence(10, (10, 3), seed=1)
    sym = bpsk_modulate(np.array([0, 1, 0, 1, 1], dtype=np.int8))
    chips = spread(sym, pn)
    assert chips.size == sym.size * pn.size


def test_clean_roundtrip_recovers_symbols_exactly() -> None:
    """No-noise round-trip: despread yields ``+/- 1 + 0j`` exactly."""
    pn = generate_m_sequence(10, (10, 3), seed=1)
    bits = np.array([0, 1, 1, 0, 0, 1, 0, 1], dtype=np.int8)
    sym = bpsk_modulate(bits)
    chips = spread(sym, pn)
    rec = despread(chips, pn)
    np.testing.assert_allclose(rec, sym, atol=1e-7)
    np.testing.assert_array_equal(bpsk_demodulate(rec), bits)


def test_processing_gain_against_unsynced_interferer() -> None:
    """Co-channel interferer not synced to PN is suppressed by ~SF.

    Models an interferer at the same chip rate but with a DIFFERENT
    PN sequence (different seed). The despreader's correlator
    coherent-sums the wanted symbol over ``N = 1023`` chips while
    the interferer's chips average toward the m-sequence's two-level
    autocorrelation sidelobe (``-1 / N`` after normalisation). The
    output wanted-to-interferer power ratio gains by
    approximately ``10 * log10(N)`` = ~30.1 dB.

    Concrete check: wanted output magnitude must be at least 100x
    (40 dB headroom against the 30 dB target) the worst-case
    interferer's despread magnitude. Conservative pass band -- the
    test fails loudly if PG collapses (e.g. a bug that
    accidentally drops the normalisation).
    """
    pn_wanted = generate_m_sequence(10, (10, 3), seed=1)
    pn_interferer = generate_m_sequence(10, (10, 3), seed=42)
    # One +1 wanted symbol.
    wanted_chips = spread(np.array([1.0 + 0.0j], dtype=np.complex64), pn_wanted)
    # One +1 interferer symbol of equal channel power (also +/- 1).
    interferer_chips = spread(np.array([1.0 + 0.0j], dtype=np.complex64), pn_interferer)
    combined = wanted_chips + interferer_chips
    out = despread(combined, pn_wanted)
    # Output: wanted should be ~+1; the interferer contribution is
    # autocorrelation sidelobe magnitude / N = 1 / 1023.
    wanted_magnitude = float(np.abs(out[0]))
    # Interferer leakage at the despread output: bounded by the
    # (cyclic-shifted) cross-correlation magnitude / N.
    interferer_only_out = despread(interferer_chips, pn_wanted)
    interferer_leakage = float(np.abs(interferer_only_out[0]))
    assert wanted_magnitude > 100 * interferer_leakage
    assert wanted_magnitude == pytest.approx(1.0, abs=0.01)


def test_wrong_pn_phase_yields_noise_floor() -> None:
    """Despreading with a rotated PN gives ~``-1 / N`` magnitude (sidelobe)."""
    pn = generate_m_sequence(10, (10, 3), seed=1)
    pn_shifted = np.roll(pn, 5)
    sym = bpsk_modulate(np.array([0], dtype=np.int8))  # +1 symbol
    chips = spread(sym, pn)
    rec = despread(chips, pn_shifted)
    # Two-level autocorr sidelobe is exactly ``-1 / N`` for m-sequence.
    expected_magnitude = 1.0 / pn.size
    assert float(np.abs(rec[0])) == pytest.approx(expected_magnitude, abs=1e-6)


def test_despread_rejects_partial_symbol_input() -> None:
    """``len(chips) % len(pn) != 0`` raises (sync/framing bug surface)."""
    pn = generate_m_sequence(10, (10, 3), seed=1)
    bad_chips = np.zeros(pn.size + 1, dtype=np.complex64)
    with pytest.raises(DsssError, match="multiple of pn length"):
        despread(bad_chips, pn)


def test_spread_rejects_multi_dim_inputs() -> None:
    """1-D-only contract on both args."""
    pn = generate_m_sequence(10, (10, 3), seed=1)
    with pytest.raises(DsssError, match="symbols must be 1-D"):
        spread(np.ones((2, 4), dtype=np.complex64), pn)
    with pytest.raises(DsssError, match="pn must be 1-D"):
        spread(np.ones(4, dtype=np.complex64), pn.reshape(-1, 1))


def test_golden_match() -> None:
    """Freshly-computed round-trip matches the checked-in golden artefact."""
    npz = np.load(_GOLDEN)
    pn = generate_m_sequence(10, (10, 3), seed=1)
    bits = npz["bits"]
    sym = bpsk_modulate(bits)
    chips = spread(sym, pn)
    rec_sym = despread(chips, pn)
    rec_bits = bpsk_demodulate(rec_sym)
    np.testing.assert_array_equal(pn, npz["pn"])
    np.testing.assert_array_equal(sym, npz["symbols"])
    np.testing.assert_array_equal(chips, npz["chips"])
    np.testing.assert_allclose(rec_sym, npz["recovered_symbols"], atol=1e-7)
    np.testing.assert_array_equal(rec_bits, npz["recovered_bits"])
