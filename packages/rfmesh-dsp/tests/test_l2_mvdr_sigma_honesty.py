"""Sigma-honesty Monte Carlo for L2 Capon (WS-B-004 Acceptance 3).

For each SNR in {10, 20, 30} dB the test runs 200 coherent reads using
``SyntheticReceiver.reseed(i)`` to perturb noise while keeping the
deterministic emitter geometry fixed, collects each trial's recovered
azimuth and claimed sigma, and asserts

    0.8 * median(sigma_claimed) <= std(recovered_azimuth) <= 1.2 * median(sigma_claimed).

The empirical std is the *truth* about how spread the estimator's output
actually is; the median of claimed sigmas is what the estimator
*promises*. The +/-20 % band protects fusion from sigma claims that are
systematically optimistic (driving over-weighted bearings into a fix) or
systematically pessimistic (down-weighting good bearings).

PER THE TICKET: do NOT widen this tolerance to make the test pass. If
the band breaks, the sigma estimator is wrong, not the test -- re-derive
``_CAPON_SIGMA_K`` (and the ``_SIGMA_LOADING_FACTOR`` if necessary) and
update the scratchpad note.

The Capon sigma path uses a different recipe from L1's polyfit-cov
(within-realisation residuals are essentially zero for Capon because all
scan samples share one R); see ``l2_mvdr.py`` module docstring section
"WHY SPLIT LOADING FOR PEAK VS SIGMA" and
``.claude/scratchpad/ws-b-2026-05-16.md`` for the derivation.

Slow (~10 s total: 3 SNRs x 200 trials x 4096 coherent samples each).
Marked ``slow`` so locally a developer can skip with ``-m 'not slow'``,
but it remains in the default suite because the honesty guarantee is
load-bearing for fusion weighting.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    ArrayConfig,
    ArrayGeometry,
    GeodeticPosition,
)
from rfmesh_dsp import L2MvdrEstimator
from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
    SimulationScenario,
    SyntheticReceiver,
)

_NODE_ID = "honesty-mvdr-node"
_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)
_T_UNIX_NS = 1_700_000_000_000_000_000
_CENTER_FREQ_HZ = 915e6
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_WAVELENGTH_M = _SPEED_OF_LIGHT_M_PER_S / _CENTER_FREQ_HZ
_UCA_RADIUS_M = 0.25 * _WAVELENGTH_M
_N_COHERENT_SAMPLES = 4096
_PEAK_AZIMUTH_DEG = 137.0
_HONESTY_SNRS_DB: tuple[float, ...] = (10.0, 20.0, 30.0)
_HONESTY_TRIALS = 200
_HONESTY_BAND = 0.20


@pytest.mark.slow
@pytest.mark.parametrize("snr_db", _HONESTY_SNRS_DB)
def test_empirical_spread_matches_claimed_sigma(
    snr_db: float,
    mvdr_uca4_snr_scenario_factory: Callable[[float], SimulationScenario],
) -> None:
    """At each SNR, median claimed sigma matches empirical spread within +/-20 %."""
    scenario = mvdr_uca4_snr_scenario_factory(snr_db)
    # One receiver across all trials, reseeded per trial -- the same
    # reseed-without-touching-geometry pattern WS-B-001's sigma honesty
    # test uses.
    receiver = SyntheticReceiver(scenario, seed=0)
    receiver.open()
    receiver.calibrate()
    estimator = L2MvdrEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        receiver=receiver,
        array_config=ArrayConfig(
            geometry=ArrayGeometry.UCA,
            n_elements=4,
            element_spacing_m=_UCA_RADIUS_M,
        ),
        operating_frequency_hz=_CENTER_FREQ_HZ,
    )

    recovered_az: list[float] = []
    claimed_sigma: list[float] = []
    for trial in range(_HONESTY_TRIALS):
        receiver.reseed(trial)
        estimator.set_timestamp(_T_UNIX_NS)
        block = receiver.read_coherent(_N_COHERENT_SAMPLES)
        report = estimator.estimate(block)
        if report is not None:
            recovered_az.append(float(report.azimuth_deg))
            claimed_sigma.append(float(report.azimuth_sigma_deg))
    receiver.close()

    assert len(claimed_sigma) >= int(0.9 * _HONESTY_TRIALS), (
        f"SNR={snr_db} dB: only {len(claimed_sigma)}/{_HONESTY_TRIALS} trials "
        "produced a bearing; the prominence or cond gate is rejecting too "
        "aggressively for this SNR."
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
        f"SNR={snr_db} dB: empirical sigma {sigma_emp:.5f} deg not within "
        f"+/-{int(_HONESTY_BAND * 100)} % of median claimed sigma "
        f"{sigma_claimed:.5f} deg "
        f"(allowed band [{lower:.5f}, {upper:.5f}])."
    )
