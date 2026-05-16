"""Sigma-honesty Monte Carlo: empirical spread vs claimed sigma (Acceptance 3).

For each SNR in {10, 20, 30} dB the test runs 200 sweeps using
``SyntheticReceiver.reseed(i)`` to perturb noise while keeping the
deterministic emitter geometry fixed, collects each trial's recovered
azimuth and claimed sigma, and asserts

    0.8 * median(sigma_claimed) <= std(recovered_azimuth) <= 1.2 * median(sigma_claimed).

The empirical std is the *truth* about how spread the estimator's output
actually is; the median of claimed sigmas is what the estimator *promises*.
A 20 % band protects fusion from sigma claims that are systematically
optimistic (driving over-weighted bearings into a fix) or systematically
pessimistic (down-weighting good bearings).

PER THE TICKET: do NOT widen this tolerance to make the test pass. If the
band breaks, the sigma estimator is wrong, not the test -- stop and write
a scratchpad note (WS-B-001 Stop conditions).

Slow (~75 s total: 3 SNRs x 200 trials x 360 reads x 8192 samples each).
Marked ``slow`` so locally a developer can skip with ``-m 'not slow'``,
but it remains in the default suite because the honesty guarantee is
load-bearing for fusion weighting (see ``INTERFACES.md`` Section 3 on
``BearingReport.azimuth_sigma_deg``).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from rfmesh_contracts import GeodeticPosition  # type: ignore[import-untyped, unused-ignore]
from rfmesh_dsp import L1AmplitudeSweepEstimator
from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
    SimulationScenario,
    SyntheticReceiver,
)

_NODE_ID = "honesty-node"
_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)
_T_UNIX_NS = 1_700_000_000_000_000_000
_SWEEP_BLOCK_SAMPLES = 8192
_SWEEP_STEP_DEG = 1.0
_PEAK_AZIMUTH_DEG = 137.0
_HONESTY_SNRS_DB: tuple[float, ...] = (10.0, 20.0, 30.0)
_HONESTY_TRIALS = 200
_HONESTY_BAND = 0.20


@pytest.mark.slow
@pytest.mark.parametrize("snr_db", _HONESTY_SNRS_DB)
def test_empirical_spread_matches_claimed_sigma(
    snr_db: float,
    snr_scenario_factory: Callable[[float], SimulationScenario],
) -> None:
    """At each SNR, median claimed sigma matches empirical spread within +/-20 %."""
    scenario = snr_scenario_factory(snr_db)
    # One receiver across all trials, reseeded per trial -- this is the
    # exact pattern the ticket calls out: "reseed(i) to perturb noise
    # without touching geometry".
    receiver = SyntheticReceiver(scenario, seed=0)
    receiver.open()
    estimator = L1AmplitudeSweepEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        sweep_step_deg=_SWEEP_STEP_DEG,
        sweep_dwell_samples=_SWEEP_BLOCK_SAMPLES,
    )

    recovered_az: list[float] = []
    claimed_sigma: list[float] = []
    for trial in range(_HONESTY_TRIALS):
        receiver.reseed(trial)
        estimator.begin_sweep(t_unix_ns=_T_UNIX_NS)
        for h in np.arange(0.0, 360.0, _SWEEP_STEP_DEG):
            receiver.set_antenna_heading(float(h))
            iq = receiver.read(_SWEEP_BLOCK_SAMPLES)
            estimator.observe(float(h), iq)
        report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
        if report is not None:
            recovered_az.append(float(report.azimuth_deg))
            claimed_sigma.append(float(report.azimuth_sigma_deg))

    receiver.close()

    assert len(claimed_sigma) >= int(0.9 * _HONESTY_TRIALS), (
        f"SNR={snr_db} dB: only {len(claimed_sigma)}/{_HONESTY_TRIALS} trials produced "
        "a bearing; the prominence gate is rejecting too aggressively for this SNR."
    )

    az_arr = np.asarray(recovered_az, dtype=np.float64)
    sig_arr = np.asarray(claimed_sigma, dtype=np.float64)
    # Signed shortest-arc deviation handles the 0/360 seam.
    diffs = ((az_arr - _PEAK_AZIMUTH_DEG + 180.0) % 360.0) - 180.0
    sigma_emp = float(np.std(diffs))
    sigma_claimed = float(np.median(sig_arr))

    lower = (1.0 - _HONESTY_BAND) * sigma_claimed
    upper = (1.0 + _HONESTY_BAND) * sigma_claimed
    assert lower <= sigma_emp <= upper, (
        f"SNR={snr_db} dB: empirical sigma {sigma_emp:.4f} deg not within +/-20% "
        f"of median claimed sigma {sigma_claimed:.4f} deg "
        f"(allowed band [{lower:.4f}, {upper:.4f}])."
    )
