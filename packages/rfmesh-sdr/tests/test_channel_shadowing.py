"""Acceptance criterion 1(c): LogNormalShadowing produces honest empirical sigma.

Sigma-honesty extends to channel models, not just bearings: a shadowing
channel that draws fades with a wrong standard deviation produces a
biased "noise budget" for everything downstream. We sweep 500 fresh
RNG seeds, draw one fade per scenario, and verify that the empirical
standard deviation of the per-scenario amplitude (in dB) matches the
configured ``sigma_db`` within 10 percent.
"""

from __future__ import annotations

import math

import numpy as np
from rfmesh_sdr import LogNormalShadowing

_N_TRIALS = 500
_SIGMA_DB = 4.0
_SIGMA_TOLERANCE = 0.10 * _SIGMA_DB  # ±10 percent
_DB_AMPLITUDE_DIVISOR = 20.0


def test_lognormal_shadowing_empirical_sigma() -> None:
    """500 independent ``apply`` calls reproduce ``sigma_db = 4 dB`` within ±10 percent."""
    channel = LogNormalShadowing(sigma_db=_SIGMA_DB)
    unit_sample = np.array([1.0 + 0.0j], dtype=np.complex128)

    fade_db_samples = np.empty(_N_TRIALS, dtype=np.float64)
    for trial in range(_N_TRIALS):
        # Use the trial index as the seed so each draw is independent of the
        # others -- this is the "reseed per trial" pattern the ticket spells out.
        rng = np.random.default_rng(trial)
        out = channel.apply(unit_sample, distance_m=1000.0, frequency_hz=915e6, rng=rng)
        # The fade in dB is the 20*log10 of the output amplitude (input is 1.0).
        fade_db_samples[trial] = _DB_AMPLITUDE_DIVISOR * math.log10(float(np.abs(out[0])))

    measured_sigma_db = float(np.std(fade_db_samples, ddof=1))
    error = abs(measured_sigma_db - _SIGMA_DB)
    assert error < _SIGMA_TOLERANCE, (
        f"LogNormalShadowing empirical sigma is {measured_sigma_db:.3f} dB; "
        f"expected {_SIGMA_DB:.3f} dB +/- {_SIGMA_TOLERANCE:.3f} dB."
    )

    # The empirical mean should also be near zero (the fade dB is N(0, sigma)).
    # A standard-error check: |mean| < 3 * sigma/sqrt(N) ~ 0.54 dB.
    measured_mean_db = float(np.mean(fade_db_samples))
    standard_error = _SIGMA_DB / math.sqrt(_N_TRIALS)
    assert abs(measured_mean_db) < 3.0 * standard_error, (
        f"LogNormalShadowing empirical mean is {measured_mean_db:+.3f} dB; "
        "expected approximately 0 dB."
    )
