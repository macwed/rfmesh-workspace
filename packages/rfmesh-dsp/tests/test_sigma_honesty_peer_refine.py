"""Sigma-honesty MC on the rendezvous refine-arc geometry (ADR-026 §G).

The existing ``test_sigma_honesty.py`` exercises the L1 estimator on a
full +/-180 deg sweep -- the jammer-DF geometry. ADR-026 added a peer-
acquisition path where the rendezvous loop drives a narrow refine arc
(default +/-20 deg around the GPS-prior bearing) and tags the resulting
``BearingReport`` with ``prior_kind = PEER_LINK`` + prior mean/sigma
fields. The fold is operator-supplied, but the wire's
``azimuth_sigma_deg`` is still the LIKELIHOOD sigma -- raw parabola
peak-fit residual, no prior folded in (ADR-026 §D Q2 resolution,
path (a)).

This test pins the binding regression: the estimator's likelihood sigma
on the *narrow refine arc* stays in the +/-20% MC band at SNR
{10, 20, 30} dB, exactly as it does on the full sweep. The Monte Carlo
exercises the LIKELIHOOD only; the prior fields are inert to MC sigma-
honesty by design (sigma-honesty measures across-realisation spread of
the estimator output, prior is within-realisation).

If this band breaks, the estimator IS DIFFERENT on the refine geometry
than on the full sweep -- which would be a new B2 violation needing its
own investigation, not a test-tolerance widening.
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

_NODE_ID = "peer-refine-honesty-node"
_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)
_T_UNIX_NS = 1_700_000_000_000_000_000
_SWEEP_BLOCK_SAMPLES = 8192
# Mirror RendezvousConfig defaults: refine_step_deg=2.0,
# refine_half_arc_deg=20.0 -> 21 angles centred on the peer prior.
# NOTE: the L1 estimator's default 6 dB prominence gate measures peak
# RSSI above the median off-axis floor; on a refine arc smaller than
# the antenna HPBW (50 deg for the ATK-10) the median sits IN the main
# lobe, prominence collapses, and every sweep refuses. The escalation
# ladder in RendezvousConfig (20 -> 45 -> 90 deg) widens until prominence
# passes. We test the WIDEST refine arc (90 deg half-arc = 180 deg
# total -- still narrower than the full +/-180 deg jammer sweep) so
# the prominence gate has off-axis data to compare against and the
# likelihood-sigma honesty band is exercisable.
_REFINE_STEP_DEG = 2.0
_REFINE_HALF_ARC_DEG = 90.0
# The scenario factory places the emitter at 137 deg; treat it as the
# peer beacon for the refine geometry.
_PEER_BEACON_AZIMUTH_DEG = 137.0
_HONESTY_SNRS_DB: tuple[float, ...] = (10.0, 20.0, 30.0)
_HONESTY_TRIALS = 200
_HONESTY_BAND = 0.20


@pytest.mark.slow
@pytest.mark.parametrize("snr_db", _HONESTY_SNRS_DB)
def test_likelihood_sigma_honesty_on_refine_arc(
    snr_db: float,
    snr_scenario_factory: Callable[[float], SimulationScenario],
) -> None:
    """Refine-arc likelihood sigma stays in +/-20% MC band at each SNR."""
    scenario = snr_scenario_factory(snr_db)
    receiver = SyntheticReceiver(scenario, seed=0)
    receiver.open()
    estimator = L1AmplitudeSweepEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        sweep_step_deg=_REFINE_STEP_DEG,
        sweep_dwell_samples=_SWEEP_BLOCK_SAMPLES,
    )

    arc_min = _PEER_BEACON_AZIMUTH_DEG - _REFINE_HALF_ARC_DEG
    arc_max = _PEER_BEACON_AZIMUTH_DEG + _REFINE_HALF_ARC_DEG
    headings = np.arange(arc_min, arc_max + _REFINE_STEP_DEG, _REFINE_STEP_DEG)

    recovered_az: list[float] = []
    claimed_sigma: list[float] = []
    for trial in range(_HONESTY_TRIALS):
        receiver.reseed(trial)
        estimator.begin_sweep(t_unix_ns=_T_UNIX_NS)
        for h in headings:
            receiver.set_antenna_heading(float(h))
            iq = receiver.read(_SWEEP_BLOCK_SAMPLES)
            estimator.observe(float(h), iq)
        report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
        if report is not None:
            recovered_az.append(float(report.azimuth_deg))
            # The estimator returns the LIKELIHOOD sigma -- the tagging
            # of prior_kind / prior_mean / prior_sigma happens at the
            # rendezvous loop layer (RendezvousLoop._tag_peer_prior),
            # NOT in the estimator. We test the unfolded likelihood
            # sigma here, which is what travels on the wire's
            # azimuth_sigma_deg.
            claimed_sigma.append(float(report.azimuth_sigma_deg))

    receiver.close()

    assert len(claimed_sigma) >= int(0.9 * _HONESTY_TRIALS), (
        f"SNR={snr_db} dB: only {len(claimed_sigma)}/{_HONESTY_TRIALS} trials produced "
        "a bearing on the refine arc; the prominence gate is too tight here."
    )

    az_arr = np.asarray(recovered_az, dtype=np.float64)
    sig_arr = np.asarray(claimed_sigma, dtype=np.float64)
    diffs = ((az_arr - _PEER_BEACON_AZIMUTH_DEG + 180.0) % 360.0) - 180.0
    sigma_emp = float(np.std(diffs))
    sigma_claimed = float(np.median(sig_arr))

    lower = (1.0 - _HONESTY_BAND) * sigma_claimed
    upper = (1.0 + _HONESTY_BAND) * sigma_claimed
    assert lower <= sigma_emp <= upper, (
        f"REFINE SNR={snr_db} dB: empirical sigma {sigma_emp:.4f} deg "
        f"not within +/-20% of median claimed sigma {sigma_claimed:.4f} deg "
        f"(allowed band [{lower:.4f}, {upper:.4f}]). This is a B2 violation "
        "on the refine geometry -- do NOT widen the band; investigate the "
        "estimator's behaviour at narrow arc widths."
    )
