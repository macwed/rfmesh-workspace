"""Posterior bearing combiner -- one helper, every consumer keys off it.

ADR-026 §I (v1.4.0 binding). Combines a Gaussian likelihood + Gaussian
prior on a circular azimuth axis into a single posterior. Lives here
(``rfmesh-fusion``) because:

1. The wire ``BearingReport.azimuth_sigma_deg`` is the LIKELIHOOD sigma;
   prior travels on ``prior_mean_deg`` / ``prior_sigma_deg``. Posterior
   is NEVER on the wire -- keeps the MC sigma-honesty test (B2) coherent.
2. Multiple consumers (``link.html`` peer-bearing render, future
   Kalman tracker, posterior log analytics) need the same formula. RF-DSP
   NOTE 2 on ADR-026: "two consumers reimplementing inconsistently is a
   real hazard; one might forget the circular wrap."

The sigma combine is the standard inverse-variance formula
``1/sigma_post**2 = 1/sigma_L**2 + 1/sigma_P**2``. The mean combine
respects 0/360 deg wrap by converting both inputs to unit vectors,
weighting by ``1/sigma**2``, averaging in Cartesian, and back to a
wrapped angle.
"""

from __future__ import annotations

import math

_DEG_FULL_CIRCLE: float = 360.0


def combine_bearing_prior(
    likelihood_mean_deg: float,
    likelihood_sigma_deg: float,
    prior_mean_deg: float,
    prior_sigma_deg: float,
) -> tuple[float, float]:
    """Combine a Gaussian likelihood + Gaussian prior on a circular axis.

    Args:
        likelihood_mean_deg: The estimator's azimuth, degrees CW from
            north in ``[0, 360)``. From ``BearingReport.azimuth_deg``.
        likelihood_sigma_deg: The estimator's 1-sigma uncertainty,
            degrees, strictly positive. From
            ``BearingReport.azimuth_sigma_deg`` (the LIKELIHOOD sigma,
            ADR-026 path (a) -- not posterior).
        prior_mean_deg: The prior azimuth, degrees CW from north in
            ``[0, 360)``. From ``BearingReport.prior_mean_deg``.
        prior_sigma_deg: The prior 1-sigma uncertainty, degrees,
            strictly positive. From ``BearingReport.prior_sigma_deg``.

    Returns:
        ``(posterior_mean_deg, posterior_sigma_deg)``. The mean is in
        ``[0, 360)``. The sigma is in degrees, strictly positive, and
        always strictly smaller than each input sigma (information
        adds; uncertainty contracts).

    Raises:
        ValueError: any sigma is non-positive. We do not silently
            substitute a fallback (B3).
    """
    if likelihood_sigma_deg <= 0.0:
        msg = (
            f"combine_bearing_prior: likelihood_sigma_deg must be > 0; got {likelihood_sigma_deg}."
        )
        raise ValueError(msg)
    if prior_sigma_deg <= 0.0:
        msg = f"combine_bearing_prior: prior_sigma_deg must be > 0; got {prior_sigma_deg}."
        raise ValueError(msg)

    # Inverse-variance weighting in Cartesian (unit-vector) space so the
    # 0/360 deg wrap is handled correctly. A naive scalar average of
    # 359 deg and 1 deg gives 180 deg; the Cartesian average gives 0 deg.
    w_l = 1.0 / (likelihood_sigma_deg * likelihood_sigma_deg)
    w_p = 1.0 / (prior_sigma_deg * prior_sigma_deg)

    l_rad = math.radians(likelihood_mean_deg)
    p_rad = math.radians(prior_mean_deg)
    x = w_l * math.cos(l_rad) + w_p * math.cos(p_rad)
    y = w_l * math.sin(l_rad) + w_p * math.sin(p_rad)
    posterior_rad = math.atan2(y, x)
    posterior_mean_deg = math.degrees(posterior_rad) % _DEG_FULL_CIRCLE

    # Sigma combine is inverse-variance addition. Independent of the
    # circular wrap; identical to the linear case.
    posterior_var = 1.0 / (w_l + w_p)
    posterior_sigma_deg = math.sqrt(posterior_var)

    return posterior_mean_deg, posterior_sigma_deg


__all__ = ["combine_bearing_prior"]
