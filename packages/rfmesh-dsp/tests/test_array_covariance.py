"""WS-B-002 sample-covariance and forward-backward smoothing tests.

The tests cover three layers:

* Algebraic properties that hold for any complex IQ block (Hermitian, PSD,
  forward-backward Hermitian preservation).
* Snapshot-averaging arithmetic: ``R(K splits) == R(None)`` for any block,
  with a deterministic-tile vs random-noise contrast that demonstrates *why*
  the K parameter matters in practice.
* Forward-backward applied to a single-source rank-1 R built from a ULA
  steering vector preserves the dominant eigenvector up to a global phase.

Plus a golden-file regression: re-derive ``R`` from a fixed deterministic
seed and assert byte-near equality against the committed ``.npz``.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    ArrayGeometry,
)
from rfmesh_dsp.array_covariance import forward_backward_smooth, sample_covariance
from rfmesh_dsp.array_manifold import steering_vector

_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_REFERENCE_FREQ_HZ = 915e6
_REFERENCE_WAVELENGTH_M = _SPEED_OF_LIGHT_M_PER_S / _REFERENCE_FREQ_HZ
_HERMITIAN_TOLERANCE = 1e-12
_PSD_FLOOR = -1e-12
# Lower bound on |R_full - R_first_segment| for the random-noise sub-check
# of test_sample_covariance_n_snapshots_averaging. Picked from the typical
# ~0.1 sample-variance spread for 128-sample standard complex normal; well
# above round-off and well below the 1.0 dynamic range, so the assertion
# fires only on a genuinely vacuous setup.
_RANDOM_SEGMENT_DIFF_FLOOR = 0.05


def _random_iq(
    rng: np.random.Generator,
    n_channels: int,
    n_samples: int,
    *,
    dtype: type = np.complex64,
) -> np.ndarray:
    real = rng.standard_normal((n_channels, n_samples))
    imag = rng.standard_normal((n_channels, n_samples))
    return (real + 1j * imag).astype(dtype)


# ---------------------------------------------------------------------------
# Sample-covariance algebra
# ---------------------------------------------------------------------------


def test_sample_covariance_hermitian() -> None:
    """``R = R^H`` to round-off for any complex IQ block."""
    rng = np.random.default_rng(7)
    block = _random_iq(rng, n_channels=4, n_samples=256)
    cov = sample_covariance(block)
    np.testing.assert_allclose(cov, cov.conj().T, atol=_HERMITIAN_TOLERANCE)


def test_sample_covariance_psd() -> None:
    """All eigenvalues of R are >= 0 to round-off (R = X X^H / T is PSD by construction)."""
    rng = np.random.default_rng(11)
    block = _random_iq(rng, n_channels=4, n_samples=256)
    cov = sample_covariance(block)
    eigvals = np.linalg.eigvalsh(cov)
    assert float(eigvals.min()) >= _PSD_FLOOR, (
        f"Minimum eigenvalue {eigvals.min():.3e} drops below PSD floor {_PSD_FLOOR}."
    )


def test_sample_covariance_n_snapshots_averaging() -> None:
    """Snapshot count rebalances bookkeeping; for identical IQ it collapses.

    Two checks:

    1. Deterministic tile: a block constructed by tiling a fixed seed K
       times has ``R(block, K) == R(seed, None)`` to round-off -- because
       each per-segment covariance equals R(seed) and the K-average
       collapses to R(seed).
    2. Random noise: the same K-split on an independently random block
       yields a covariance that *differs* materially from the covariance
       of just the first segment (the K-segment average is statistically
       tighter to the true identity).
    """
    rng = np.random.default_rng(20260515)
    n_channels = 4
    seg_len = 128
    k = 8

    seed_block = _random_iq(rng, n_channels=n_channels, n_samples=seg_len)
    tiled_block = np.tile(seed_block, (1, k))
    cov_split = sample_covariance(tiled_block, n_snapshots=k)
    cov_single = sample_covariance(seed_block, n_snapshots=None)
    np.testing.assert_allclose(cov_split, cov_single, atol=_HERMITIAN_TOLERANCE)

    # And the same block with n_snapshots=None must agree with K splits --
    # the total normalisation is 1/T either way.
    cov_no_split = sample_covariance(tiled_block, n_snapshots=None)
    np.testing.assert_allclose(cov_split, cov_no_split, atol=_HERMITIAN_TOLERANCE)

    rand_block = _random_iq(rng, n_channels=n_channels, n_samples=k * seg_len)
    cov_full = sample_covariance(rand_block, n_snapshots=k)
    cov_first_seg = sample_covariance(rand_block[:, :seg_len], n_snapshots=None)
    # Independent segments make the full-block covariance and the first-
    # segment covariance disagree by more than round-off -- not a strong
    # statistical claim, just a sanity check that the deterministic case
    # above is non-vacuous.
    diff = float(np.max(np.abs(cov_full - cov_first_seg)))
    assert diff > _RANDOM_SEGMENT_DIFF_FLOOR, (
        f"random-block diff {diff:.3e} too small; the K-snapshot test may be vacuous."
    )


def test_sample_covariance_validates_inputs() -> None:
    """Bad inputs raise loudly (Invariant 4: no silent fallbacks)."""
    rng = np.random.default_rng(0)
    block = _random_iq(rng, n_channels=4, n_samples=64)
    with pytest.raises(ValueError, match="divide"):
        sample_covariance(block, n_snapshots=5)  # 5 does not divide 64
    with pytest.raises(ValueError, match=">= 1"):
        sample_covariance(block, n_snapshots=0)
    with pytest.raises(ValueError, match="2-D"):
        sample_covariance(block[0], n_snapshots=None)
    with pytest.raises(ValueError, match="complex"):
        sample_covariance(block.real.astype(np.float64), n_snapshots=None)


# ---------------------------------------------------------------------------
# Forward-backward smoothing
# ---------------------------------------------------------------------------


def test_forward_backward_preserves_hermitian() -> None:
    """R_fb stays Hermitian when R is Hermitian (it must, by construction).

    The construction is ``R_fb = (R + J R* J) / 2`` and both terms are
    Hermitian when R is, so the average is too -- the test pins this so a
    future refactor cannot quietly break it.
    """
    rng = np.random.default_rng(13)
    block = _random_iq(rng, n_channels=5, n_samples=512)
    cov = sample_covariance(block)
    smoothed = forward_backward_smooth(cov)
    np.testing.assert_allclose(smoothed, smoothed.conj().T, atol=_HERMITIAN_TOLERANCE)


def test_forward_backward_rank1_dominant_eigenvector() -> None:
    """For a ULA rank-1 ``R = a a^H``, ``R_fb`` keeps the same dominant eigenvector.

    Reasoning: for a ULA on the y-axis, the steering vector satisfies
    ``J a^* = c * a`` with ``|c| = 1`` (the conjugate-reverse of a uniform
    progressive phase ramp is a globally phase-rotated copy of the same
    ramp). So ``J R* J = (J a^*)(J a^*)^H = a a^H = R``, and
    ``R_fb = R`` -- the dominant eigenvector is unchanged.
    """
    n = 6
    spacing_m = 0.5 * _REFERENCE_WAVELENGTH_M
    positions = np.zeros((n, 2), dtype=np.float64)
    positions[:, 1] = np.arange(n, dtype=np.float64) * spacing_m
    a_vec = steering_vector(
        geometry=ArrayGeometry.ULA,
        element_positions_m=positions,
        azimuth_rad=math.radians(40.0),
        wavelength_m=_REFERENCE_WAVELENGTH_M,
    )
    cov = np.outer(a_vec, a_vec.conj())
    smoothed = forward_backward_smooth(cov)

    eigvals, eigvecs = np.linalg.eigh(smoothed)
    dominant = eigvecs[:, int(np.argmax(eigvals))]
    overlap = float(abs(np.vdot(dominant, a_vec)))
    assert overlap == pytest.approx(1.0, abs=1e-10), (
        f"|<dominant, a>| = {overlap:.10f}, expected 1.0 (rank-1 ULA invariance)."
    )


def test_forward_backward_validates_inputs() -> None:
    """Non-square or non-2-D inputs raise."""
    with pytest.raises(ValueError, match="square"):
        forward_backward_smooth(np.zeros((4, 5), dtype=np.complex128))
    with pytest.raises(ValueError, match="2-D"):
        forward_backward_smooth(np.zeros(4, dtype=np.complex128))


# ---------------------------------------------------------------------------
# Golden-file regression (Invariant 3)
# ---------------------------------------------------------------------------


def test_golden_covariance_known_seed(golden_dir: Path) -> None:
    """Re-deriving R from the committed seed reproduces the committed R byte-near."""
    fixture = np.load(golden_dir / "covariance_known_seed.npz")
    seed = int(fixture["seed"])
    n_channels = int(fixture["n_channels"])
    n_samples = int(fixture["n_samples"])
    expected = fixture["covariance"]

    rng = np.random.default_rng(seed)
    real = rng.standard_normal((n_channels, n_samples))
    imag = rng.standard_normal((n_channels, n_samples))
    block = (real + 1j * imag).astype(np.complex64)
    # The block stored in the fixture must match what we just regenerated;
    # if it does not, the seed contract itself has drifted and the golden
    # is meaningless.
    np.testing.assert_array_equal(block, fixture["block"])

    actual = sample_covariance(block)
    assert actual.shape == expected.shape == (n_channels, n_channels)
    assert actual.dtype == expected.dtype == np.complex128
    max_abs_diff = float(np.max(np.abs(actual - expected)))
    assert max_abs_diff <= _HERMITIAN_TOLERANCE, (
        f"Covariance golden drift: max-abs diff {max_abs_diff:.3e}"
    )
