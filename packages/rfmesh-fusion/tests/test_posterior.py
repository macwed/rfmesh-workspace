"""Tests for combine_bearing_prior (ADR-026 §I posterior helper)."""

from __future__ import annotations

import math

import pytest
from rfmesh_fusion import combine_bearing_prior


def test_equal_inputs_return_input_mean_with_smaller_sigma() -> None:
    """Two identical Gaussians combine to the same mean with sqrt(1/2) sigma."""
    post_mean, post_sigma = combine_bearing_prior(
        likelihood_mean_deg=142.0,
        likelihood_sigma_deg=5.0,
        prior_mean_deg=142.0,
        prior_sigma_deg=5.0,
    )
    assert post_mean == pytest.approx(142.0, abs=1e-9)
    # 1/sigma_post^2 = 2/25 -> sigma_post = sqrt(12.5).
    assert post_sigma == pytest.approx(math.sqrt(12.5), rel=1e-9)


def test_tight_prior_pulls_mean_toward_prior() -> None:
    """Inverse-variance weighting: tighter input dominates."""
    post_mean, post_sigma = combine_bearing_prior(
        likelihood_mean_deg=140.0,
        likelihood_sigma_deg=10.0,
        prior_mean_deg=142.0,
        prior_sigma_deg=1.0,
    )
    # Prior weight is 100x likelihood weight; posterior mean very near 142.
    assert post_mean == pytest.approx(141.98, abs=0.05)
    # Posterior sigma always smaller than the tighter input.
    assert post_sigma < 1.0


def test_zero_crossing_wrap_handled() -> None:
    """A naive scalar average of 359 deg and 1 deg = 180; circular = ~0."""
    post_mean, _ = combine_bearing_prior(
        likelihood_mean_deg=359.0,
        likelihood_sigma_deg=1.0,
        prior_mean_deg=1.0,
        prior_sigma_deg=1.0,
    )
    # Posterior should be ~0 deg (or equivalently very close to 360).
    wrapped = min(post_mean, 360.0 - post_mean)
    assert wrapped == pytest.approx(0.0, abs=0.01)


def test_posterior_sigma_strictly_smaller_than_either_input() -> None:
    """Information adds; uncertainty contracts."""
    _, post_sigma = combine_bearing_prior(
        likelihood_mean_deg=100.0,
        likelihood_sigma_deg=7.0,
        prior_mean_deg=120.0,
        prior_sigma_deg=3.0,
    )
    assert post_sigma < 3.0
    assert post_sigma < 7.0


def test_negative_likelihood_sigma_refused() -> None:
    """B3: non-positive sigma is a loud refusal, not silent substitution."""
    with pytest.raises(ValueError, match="likelihood_sigma_deg must be > 0"):
        combine_bearing_prior(
            likelihood_mean_deg=0.0,
            likelihood_sigma_deg=-1.0,
            prior_mean_deg=0.0,
            prior_sigma_deg=1.0,
        )


def test_zero_prior_sigma_refused() -> None:
    with pytest.raises(ValueError, match="prior_sigma_deg must be > 0"):
        combine_bearing_prior(
            likelihood_mean_deg=0.0,
            likelihood_sigma_deg=1.0,
            prior_mean_deg=0.0,
            prior_sigma_deg=0.0,
        )


def test_returns_mean_in_zero_to_360() -> None:
    """Output is always wrapped to [0, 360]. The mod operation can yield
    exactly 0.0 or, with floating-point rounding, very close to 360.0;
    both are valid representations of "north" on the circle."""
    for ll, pp in [(355.0, 5.0), (180.0, 180.0), (270.0, 90.0)]:
        post_mean, _ = combine_bearing_prior(
            likelihood_mean_deg=ll,
            likelihood_sigma_deg=2.0,
            prior_mean_deg=pp,
            prior_sigma_deg=2.0,
        )
        assert 0.0 <= post_mean <= 360.0
