"""Tests for ``L2MvdrEstimator`` (WS-B-004).

Covers Acceptance 1 and 2 of WS-B-004:

* Protocol conformance (``BearingEstimator``).
* ``method == Capability.L2_MVDR_NULL``.
* Peak recovery within +/-2 deg at 20 dB SNR on the canonical UCA-4
  fixture (Capon's peak is broader than MUSIC's; the tolerance is
  looser than WS-B-003's +/-1 deg).
* ``None`` on an uncalibrated coherent stream (Invariant 4 surface).
* ``None`` on a -10 dB SNR scenario (no clear peak).
* ``None`` when ``set_timestamp`` has not been called.
* Re-arm: after one ``estimate``, the next call returns ``None`` until
  ``set_timestamp`` is invoked again.
* Diagonal loading stabilises R_loaded at SNR = 0 dB (cond <= 1e6).
* Golden pseudospectrum (seed = 42) byte-near-equal to the committed
  fixture; golden sigma table within 5 % relative.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import conftest  # type: ignore[import-not-found, unused-ignore]
import golden_generator  # type: ignore[import-not-found, unused-ignore]
import numpy as np
import pytest
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    ArrayConfig,
    ArrayGeometry,
    BearingEstimator,
    Capability,
    GeodeticPosition,
)
from rfmesh_dsp import L2MvdrEstimator
from rfmesh_dsp.array_covariance import sample_covariance
from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
    SimulationScenario,
    SyntheticReceiver,
)

_NODE_ID = "test-l2-node"
_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)
_T_UNIX_NS = 1_700_000_000_000_000_000
_CENTER_FREQ_HZ = 915e6
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_WAVELENGTH_M = _SPEED_OF_LIGHT_M_PER_S / _CENTER_FREQ_HZ
_UCA_RADIUS_M = 0.25 * _WAVELENGTH_M
_N_COHERENT_SAMPLES = 4096
_PEAK_AZIMUTH_DEG = 137.0
_PEAK_TOLERANCE_DEG = 2.0
_GOLDEN_DIR = Path(__file__).parent / "golden"
# The pseudospectrum is a deterministic function of a fixed-seed RNG and
# of complex128 numerics; allow only float64 round-off across numpy
# micro-versions.
_GOLDEN_PSPEC_MAX_ABS_REL_DIFF = 1e-9
_GOLDEN_SIGMA_TABLE_REL_TOL = 0.05
# Condition-number ceiling for the SNR=0 dB stability test (looser than
# the estimator's internal 1e8 reject gate; tightens the assertion that
# loading actually does its job at low SNR).
_LOW_SNR_COND_CEILING = 1e6
_LOW_SNR_FOR_STABILITY_DB = 0.0
_LOW_SNR_REJECT_DB = -10.0


def _array_config_uca4() -> ArrayConfig:
    """ArrayConfig matching the ``mvdr_uca4_scenario`` simulator fixture."""
    return ArrayConfig(
        geometry=ArrayGeometry.UCA,
        n_elements=4,
        element_spacing_m=_UCA_RADIUS_M,
    )


def _build_calibrated_receiver(
    scenario: SimulationScenario,
    seed: int = 0,
) -> SyntheticReceiver:
    receiver = SyntheticReceiver(scenario, seed=seed)
    receiver.open()
    receiver.calibrate()
    return receiver


def _build_estimator(receiver: SyntheticReceiver) -> L2MvdrEstimator:
    return L2MvdrEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        receiver=receiver,
        array_config=_array_config_uca4(),
        operating_frequency_hz=_CENTER_FREQ_HZ,
    )


def test_protocol_conformance(mvdr_uca4_scenario: SimulationScenario) -> None:
    """``BearingEstimator`` is runtime-checkable; the MVDR class is one."""
    receiver = _build_calibrated_receiver(mvdr_uca4_scenario)
    estimator = _build_estimator(receiver)
    assert isinstance(estimator, BearingEstimator)
    receiver.close()


def test_method_is_l2_mvdr(mvdr_uca4_scenario: SimulationScenario) -> None:
    """``method`` advertises the MVDR capability so the runtime can route by it."""
    receiver = _build_calibrated_receiver(mvdr_uca4_scenario)
    estimator = _build_estimator(receiver)
    assert estimator.method is Capability.L2_MVDR_NULL
    receiver.close()


def test_peak_recovers_known_angle_uca(mvdr_uca4_scenario: SimulationScenario) -> None:
    """At 20 dB SNR a single coherent block recovers 137 deg within +/-2 deg."""
    receiver = _build_calibrated_receiver(mvdr_uca4_scenario, seed=0)
    estimator = _build_estimator(receiver)
    estimator.set_timestamp(_T_UNIX_NS)
    block = receiver.read_coherent(_N_COHERENT_SAMPLES)
    report = estimator.estimate(block)
    receiver.close()

    assert report is not None
    assert report.method is Capability.L2_MVDR_NULL
    assert report.node_id == _NODE_ID
    assert report.t_unix_ns == _T_UNIX_NS
    # Signed shortest-arc deviation handles the 0/360 seam correctly.
    diff = ((report.azimuth_deg - _PEAK_AZIMUTH_DEG + 180.0) % 360.0) - 180.0
    assert abs(diff) <= _PEAK_TOLERANCE_DEG, (
        f"recovered azimuth {report.azimuth_deg:.3f} deg differs from "
        f"{_PEAK_AZIMUTH_DEG} deg by {diff:+.3f} deg (> {_PEAK_TOLERANCE_DEG} deg)."
    )
    assert report.azimuth_sigma_deg > 0.0


def test_returns_none_on_uncalibrated_stream(mvdr_uca4_scenario: SimulationScenario) -> None:
    """The L2 DSP refuses to emit on a coherent stream that was never calibrated."""
    receiver = SyntheticReceiver(mvdr_uca4_scenario, seed=0)
    receiver.open()
    # Deliberately skip receiver.calibrate(); is_calibrated stays False.
    estimator = _build_estimator(receiver)
    estimator.set_timestamp(_T_UNIX_NS)
    block = receiver.read_coherent(_N_COHERENT_SAMPLES)
    report = estimator.estimate(block)
    receiver.close()
    assert report is None


def test_returns_none_on_low_snr(
    mvdr_uca4_snr_scenario_factory: Callable[[float], SimulationScenario],
) -> None:
    """At -10 dB SNR the Capon spectrum has no clear peak; ``estimate`` returns ``None``."""
    scenario = mvdr_uca4_snr_scenario_factory(_LOW_SNR_REJECT_DB)
    receiver = _build_calibrated_receiver(scenario, seed=0)
    estimator = _build_estimator(receiver)
    nulls = 0
    n_trials = 20
    for trial in range(n_trials):
        receiver.reseed(trial)
        estimator.set_timestamp(_T_UNIX_NS)
        block = receiver.read_coherent(_N_COHERENT_SAMPLES)
        report = estimator.estimate(block)
        if report is None:
            nulls += 1
    receiver.close()
    # At -10 dB the prominence gate or the loading-stability gate should
    # reject essentially every block. Allow at most 10 % of trials to
    # leak through (the spectrum can fluctuate above the 3x-median floor
    # by luck of the realisation).
    assert nulls >= int(0.9 * n_trials), (
        f"low SNR sanity: only {nulls}/{n_trials} trials returned None; "
        "the peak-prominence or cond gate is too permissive."
    )


def test_set_timestamp_required(mvdr_uca4_scenario: SimulationScenario) -> None:
    """``estimate`` without a prior ``set_timestamp`` returns ``None``."""
    receiver = _build_calibrated_receiver(mvdr_uca4_scenario)
    estimator = _build_estimator(receiver)
    block = receiver.read_coherent(_N_COHERENT_SAMPLES)
    report = estimator.estimate(block)
    receiver.close()
    assert report is None


def test_estimate_re_arms(mvdr_uca4_scenario: SimulationScenario) -> None:
    """After ``estimate`` consumes the pending timestamp, the next call returns ``None``."""
    receiver = _build_calibrated_receiver(mvdr_uca4_scenario)
    estimator = _build_estimator(receiver)
    estimator.set_timestamp(_T_UNIX_NS)
    block = receiver.read_coherent(_N_COHERENT_SAMPLES)
    report_a = estimator.estimate(block)
    assert report_a is not None
    # No new set_timestamp -> the pending slot is empty.
    report_b = estimator.estimate(block)
    assert report_b is None
    receiver.close()


def test_diagonal_loading_stabilises_low_snr(
    mvdr_uca4_snr_scenario_factory: Callable[[float], SimulationScenario],
) -> None:
    """At SNR = 0 dB cond(R_loaded) stays below 1e6 and no result is NaN/inf."""
    scenario = mvdr_uca4_snr_scenario_factory(_LOW_SNR_FOR_STABILITY_DB)
    receiver = _build_calibrated_receiver(scenario, seed=0)
    estimator = _build_estimator(receiver)
    n_trials = 50
    max_cond = 0.0
    for trial in range(n_trials):
        receiver.reseed(trial)
        estimator.set_timestamp(_T_UNIX_NS)
        block = receiver.read_coherent(_N_COHERENT_SAMPLES)
        # Reproduce the estimator's loading recipe to measure
        # cond(R_loaded) externally; the gate inside the estimator
        # rejects above 1e8, but we want a tighter assertion here.
        cov = sample_covariance(block)
        trace_r = float(np.real(np.trace(cov)))
        epsilon = 1e-3 * trace_r / 4.0
        loaded = cov + epsilon * np.eye(4, dtype=np.complex128)
        cond = float(np.linalg.cond(loaded))
        max_cond = max(max_cond, cond)
        report = estimator.estimate(block)
        if report is not None:
            assert math.isfinite(report.azimuth_deg)
            assert math.isfinite(report.azimuth_sigma_deg)
    receiver.close()
    assert max_cond <= _LOW_SNR_COND_CEILING, (
        f"At SNR={_LOW_SNR_FOR_STABILITY_DB} dB the max cond(R_loaded) was "
        f"{max_cond:.2e}, above the {_LOW_SNR_COND_CEILING:.0e} ceiling."
    )


def test_golden_pseudospectrum(mvdr_uca4_scenario: SimulationScenario) -> None:
    """The seed=42 Capon pseudospectrum matches the committed golden array.

    Invariant 3 -- the dsp workstream's golden-file gate. Guards against
    silent numerical drift in the simulator, the manifold module, the
    covariance, or the inversion.
    """
    golden = np.load(_GOLDEN_DIR / "l2_mvdr_pseudospectrum_137deg_uca4_20db.npz")
    pspec_golden = golden["pseudospectrum"]

    receiver = SyntheticReceiver(mvdr_uca4_scenario, seed=42)
    receiver.open()
    receiver.calibrate()
    estimator = _build_estimator(receiver)
    block = receiver.read_coherent(_N_COHERENT_SAMPLES)
    pspec_now = estimator.compute_pseudospectrum(block)
    receiver.close()

    assert pspec_now.shape == pspec_golden.shape
    rel_diff = np.max(np.abs(pspec_now - pspec_golden) / np.abs(pspec_golden))
    assert float(rel_diff) <= _GOLDEN_PSPEC_MAX_ABS_REL_DIFF, (
        f"Capon pseudospectrum drifted from golden by max-abs-rel-diff {float(rel_diff):.3e}."
    )


@pytest.mark.slow
def test_golden_sigma_table() -> None:
    """Recompute the 200-trial median claimed sigma per SNR; within 5 % of golden.

    Slow (~30 s; calibration is per-trial). Marked ``slow`` so a future
    targeted run can skip it via ``-m 'not slow'``, but it stays in the
    default suite per WS-B-004 Acceptance 2.
    """
    golden = np.load(_GOLDEN_DIR / "l2_mvdr_sigma_table.npz")
    snrs = golden["snr_db"]
    median_golden = golden["median_claimed_sigma_deg"]

    for snr_db, expected_median in zip(snrs, median_golden, strict=True):
        scenario = conftest._build_mvdr_uca4_scenario(float(snr_db))
        sigmas: list[float] = []
        for trial in range(golden_generator._MVDR_SIGMA_TABLE_TRIALS):
            sigma = golden_generator.claim_mvdr_sigma_one_trial(scenario, seed=trial)
            if sigma is not None:
                sigmas.append(sigma)
        recomputed = float(np.median(sigmas))
        rel = abs(recomputed - expected_median) / expected_median
        assert rel <= _GOLDEN_SIGMA_TABLE_REL_TOL, (
            f"SNR={snr_db} dB: median sigma drifted from golden "
            f"{expected_median:.5f} -> {recomputed:.5f} ({rel * 100:.2f} %)."
        )
