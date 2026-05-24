"""Tests for the link-budget honesty helpers."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from rfmesh_dsss.exceptions import DsssError
from rfmesh_dsss.link_budget import ber_theoretical_bpsk, processing_gain_db

_GOLDEN = Path(__file__).parent / "golden" / "link_budget_ref.npz"


def test_pg_canonical_lengths() -> None:
    """``PG = 10 * log10(SF)`` at canonical m-sequence lengths.

    Reference values computed inline from ``math.log10`` so this test
    is a *functional* identity check (no separate hardcoded table
    that can drift). The expected dB values for the canonical
    spreading factors documented in the pitch (``1023 -> ~30 dB``)
    fall out of this loop.
    """
    for sf in (31, 63, 127, 255, 511, 1023, 2047):
        expected = 10.0 * math.log10(sf)
        assert processing_gain_db(sf) == pytest.approx(expected, abs=1e-12)


def test_pg_pitch_advertised_30_db_for_len1023() -> None:
    """The pitch claim ``PG ~= 30 dB at SF=1023`` holds to within 0.01 dB."""
    assert processing_gain_db(1023) == pytest.approx(30.10, abs=0.01)


def test_pg_rejects_non_positive() -> None:
    """Non-positive spreading factor raises rather than returning ``-inf``."""
    with pytest.raises(DsssError, match="spreading_factor"):
        processing_gain_db(0)


def test_ber_known_points() -> None:
    """Textbook BPSK BER at canonical Eb/N0 points (dB).

    Reference values from ``0.5 * scipy.special.erfc(sqrt(linear))``
    computed at the same precision the function uses. Pinned to
    ``rel=1e-9`` so a future numerical-precision drift (e.g. a
    library upgrade swapping erfc implementations) surfaces but the
    test is not gratuitously sensitive to non-deterministic
    floating-point noise.
    """
    cases_db = {
        0.0: 0.07864960352514258,
        3.0: 0.02287840756108532,
        6.0: 0.002388290780932807,
        9.0: 3.3627228419617505e-05,
        12.0: 9.006010350628754e-09,
    }
    for db, expected in cases_db.items():
        linear = 10.0 ** (db / 10.0)
        result = ber_theoretical_bpsk(linear)
        assert isinstance(result, float)
        assert result == pytest.approx(expected, rel=1e-9)


def test_ber_accepts_array_input() -> None:
    """Array input returns array output of matching shape and dtype."""
    eb_n0 = np.array([1.0, 10.0, 100.0], dtype=np.float64)
    out = ber_theoretical_bpsk(eb_n0)
    assert isinstance(out, np.ndarray)
    assert out.shape == eb_n0.shape
    assert out.dtype == np.float64
    # BER is strictly decreasing in Eb/N0 (monotonicity of erfc).
    assert out[0] > out[1] > out[2]


def test_ber_rejects_non_positive() -> None:
    """Eb/N0 must be strictly positive; zero/negative input raises."""
    with pytest.raises(DsssError, match="strictly positive"):
        ber_theoretical_bpsk(0.0)
    with pytest.raises(DsssError, match="strictly positive"):
        ber_theoretical_bpsk(np.array([1.0, -2.0]))


def test_pg_matches_log10_identity() -> None:
    """Functional identity: ``PG(SF) == 10 * log10(SF)``, no off-by-one drift."""
    for sf in (31, 1023, 65535):
        assert processing_gain_db(sf) == pytest.approx(10.0 * math.log10(sf), abs=1e-12)


def test_golden_match() -> None:
    """Freshly-computed PG table and BER curve match the checked-in artefact."""
    npz = np.load(_GOLDEN)
    sfs = npz["spreading_factors"]
    pg = np.array([processing_gain_db(int(sf)) for sf in sfs], dtype=np.float64)
    np.testing.assert_allclose(pg, npz["pg_db"], atol=1e-12)
    eb_n0_db = npz["eb_n0_db"]
    eb_n0_linear = 10.0 ** (eb_n0_db / 10.0)
    ber = ber_theoretical_bpsk(eb_n0_linear)
    np.testing.assert_allclose(ber, npz["ber"], rtol=1e-12)
