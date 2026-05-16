"""Tests for ``L2MusicEstimator``.

Covers WS-B-003 Acceptance 1 and 2:

* Protocol conformance (``BearingEstimator``).
* ``method == Capability.L2_MUSIC``.
* Peak recovery within +/-1 deg at 20 dB SNR on the canonical UCA-4
  scenario (tighter than L1's +/-2 deg because MUSIC out-performs
  amplitude DF).
* ``None`` on an uncalibrated coherent stream (Invariant 4 honesty gate).
* ``None`` on a low-SNR block (eigenvalue-ratio gate trips).
* ``RuntimeError`` when ``estimate`` is called without a prior
  ``set_timestamp`` (no implicit "now" fallback).
* The instance re-arms after every ``estimate`` so the next cycle starts
  clean.
* The golden pseudospectrum fixture
  ``l2_music_pseudospectrum_137deg_uca4_20db.npz`` round-trips
  bit-for-bit-ish (max abs relative diff <= 1e-9).
* The golden sigma-table fixture ``l2_music_sigma_table.npz`` matches
  recomputation within 5 % relative.
"""

from __future__ import annotations

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
from rfmesh_dsp import L2MusicEstimator
from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
    SimulationScenario,
    SyntheticReceiver,
)

_NODE_ID = "test-l2-node"
_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)
_T_UNIX_NS = 1_700_000_000_000_000_000
_OPERATING_FREQ_HZ = 915e6
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_WAVELENGTH_M = _SPEED_OF_LIGHT_M_PER_S / _OPERATING_FREQ_HZ
_UCA_N = 4
_UCA_RADIUS_M = 0.25 * _WAVELENGTH_M
_COHERENT_BLOCK_SAMPLES = 4096
_PEAK_AZIMUTH_DEG = 137.0
_PEAK_TOLERANCE_DEG = 1.0
_GOLDEN_DIR = Path(__file__).parent / "golden"
_GOLDEN_PSEUDO_REL_TOL = 1e-9
_GOLDEN_SIGMA_TABLE_REL_TOL = 0.05


def _uca_array_config() -> ArrayConfig:
    """The canonical UCA-4 array_config used by every test in this file."""
    return ArrayConfig(  # type: ignore[no-any-return, unused-ignore]
        geometry=ArrayGeometry.UCA,
        n_elements=_UCA_N,
        element_spacing_m=_UCA_RADIUS_M,
    )


def _build_estimator(receiver: SyntheticReceiver) -> L2MusicEstimator:
    """Build an L2MusicEstimator bound to ``receiver`` with the canonical geometry."""
    return L2MusicEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        receiver=receiver,
        array_config=_uca_array_config(),
        operating_frequency_hz=_OPERATING_FREQ_HZ,
    )


def _open_calibrated_receiver(scenario: SimulationScenario, seed: int) -> SyntheticReceiver:
    """Construct, open, and calibrate the coherent receiver for ``scenario``."""
    receiver = SyntheticReceiver(scenario, seed=seed)
    receiver.open()
    receiver.calibrate()
    return receiver


def test_protocol_conformance(l2_uca_scenario: SimulationScenario) -> None:
    """``BearingEstimator`` is ``@runtime_checkable``; the L2 class is one."""
    receiver = SyntheticReceiver(l2_uca_scenario, seed=0)
    estimator = _build_estimator(receiver)
    assert isinstance(estimator, BearingEstimator)


def test_method_is_l2_music(l2_uca_scenario: SimulationScenario) -> None:
    """``method`` advertises L2_MUSIC so the runtime can route by capability."""
    receiver = SyntheticReceiver(l2_uca_scenario, seed=0)
    estimator = _build_estimator(receiver)
    assert estimator.method is Capability.L2_MUSIC


def test_peak_recovers_known_angle_uca(l2_uca_scenario: SimulationScenario) -> None:
    """UCA-4 at 20 dB SNR, 4096 samples: recovered azimuth within +/-1 deg of 137 deg."""
    receiver = _open_calibrated_receiver(l2_uca_scenario, seed=0)
    estimator = _build_estimator(receiver)
    estimator.set_timestamp(_T_UNIX_NS)
    block = receiver.read_coherent(_COHERENT_BLOCK_SAMPLES)
    report = estimator.estimate(block)
    receiver.close()

    assert report is not None
    assert report.method is Capability.L2_MUSIC
    assert report.node_id == _NODE_ID
    assert report.t_unix_ns == _T_UNIX_NS
    # Signed shortest-arc deviation handles the 0/360 seam correctly.
    diff = ((report.azimuth_deg - _PEAK_AZIMUTH_DEG + 180.0) % 360.0) - 180.0
    assert abs(diff) <= _PEAK_TOLERANCE_DEG, (
        f"recovered azimuth {report.azimuth_deg:.3f} deg differs from "
        f"{_PEAK_AZIMUTH_DEG} deg by {diff:+.3f} deg (> {_PEAK_TOLERANCE_DEG} deg)."
    )
    assert report.azimuth_sigma_deg > 0.0


def test_returns_none_on_uncalibrated_stream(l2_uca_scenario: SimulationScenario) -> None:
    """Without a successful ``calibrate()``, ``estimate`` refuses the bearing.

    INTERFACES.md Section 5 binds: "L2 DSP code refuses to emit bearings
    from an uncalibrated coherent stream." The estimator's constructor
    binds to the receiver and reads ``is_calibrated`` on every cycle.
    """
    receiver = SyntheticReceiver(l2_uca_scenario, seed=0)
    receiver.open()
    # NOT calibrated -- intentionally skip receiver.calibrate().
    estimator = _build_estimator(receiver)
    estimator.set_timestamp(_T_UNIX_NS)
    block = receiver.read_coherent(_COHERENT_BLOCK_SAMPLES)
    report = estimator.estimate(block)
    receiver.close()
    assert report is None


def test_returns_none_on_low_snr(l2_low_snr_scenario: SimulationScenario) -> None:
    """At -10 dB SNR the eigenvalue-ratio gate trips and ``estimate`` returns ``None``."""
    receiver = _open_calibrated_receiver(l2_low_snr_scenario, seed=0)
    estimator = _build_estimator(receiver)
    estimator.set_timestamp(_T_UNIX_NS)
    block = receiver.read_coherent(_COHERENT_BLOCK_SAMPLES)
    report = estimator.estimate(block)
    receiver.close()
    assert report is None


def test_set_timestamp_required(l2_uca_scenario: SimulationScenario) -> None:
    """Calling ``estimate`` without ``set_timestamp`` raises RuntimeError.

    The estimator must not fabricate a ``time.time_ns()`` fallback --
    silent now-stamping would make bearing timestamps drift from the
    moment of capture.
    """
    receiver = _open_calibrated_receiver(l2_uca_scenario, seed=0)
    estimator = _build_estimator(receiver)
    block = receiver.read_coherent(_COHERENT_BLOCK_SAMPLES)
    with pytest.raises(RuntimeError, match="set_timestamp"):
        estimator.estimate(block)
    receiver.close()


def test_estimate_re_arms(l2_uca_scenario: SimulationScenario) -> None:
    """After one ``estimate`` (success or None) the instance is ready for the next cycle.

    Specifically, the pending timestamp is consumed -- calling
    ``estimate`` a second time without ``set_timestamp`` raises.
    """
    receiver = _open_calibrated_receiver(l2_uca_scenario, seed=0)
    estimator = _build_estimator(receiver)

    # Cycle 1: success.
    estimator.set_timestamp(_T_UNIX_NS)
    block_a = receiver.read_coherent(_COHERENT_BLOCK_SAMPLES)
    report_a = estimator.estimate(block_a)
    assert report_a is not None

    # The pending timestamp must have been consumed by estimate -- a
    # second call with no set_timestamp must raise.
    block_b = receiver.read_coherent(_COHERENT_BLOCK_SAMPLES)
    with pytest.raises(RuntimeError, match="set_timestamp"):
        estimator.estimate(block_b)

    # Cycle 2: a fresh set_timestamp/estimate cycle works on clean state.
    estimator.set_timestamp(_T_UNIX_NS + 1)
    block_c = receiver.read_coherent(_COHERENT_BLOCK_SAMPLES)
    report_c = estimator.estimate(block_c)
    receiver.close()
    assert report_c is not None
    assert report_c.t_unix_ns == _T_UNIX_NS + 1


def test_golden_pseudospectrum(l2_uca_scenario: SimulationScenario) -> None:
    """Recomputed pseudospectrum at seed=42 matches the committed golden array.

    Invariant 3 (the golden-file gate for the dsp workstream): guards
    against silent numerical drift in the eigendecomposition path, the
    steering manifold, or the sample-covariance estimator -- the three
    pieces this fixture composes.
    """
    golden = np.load(_GOLDEN_DIR / "l2_music_pseudospectrum_137deg_uca4_20db.npz")
    pseudo_golden = golden["pseudospectrum"]

    receiver = _open_calibrated_receiver(l2_uca_scenario, seed=42)
    estimator = _build_estimator(receiver)
    block = receiver.read_coherent(_COHERENT_BLOCK_SAMPLES)
    pseudo_now = estimator.pseudospectrum(block)
    receiver.close()

    assert pseudo_now.shape == pseudo_golden.shape
    max_rel = float(np.max(np.abs(pseudo_now - pseudo_golden) / np.abs(pseudo_golden)))
    assert max_rel <= _GOLDEN_PSEUDO_REL_TOL, (
        f"pseudospectrum drifted from golden by max-abs-rel-diff {max_rel:.3e}."
    )


@pytest.mark.slow
def test_golden_sigma_table() -> None:
    """Recompute the 200-trial median claimed sigma per SNR; check within 5 % of golden.

    Slow (~minutes). Marked ``slow`` so a future targeted run can skip
    with ``-m 'not slow'``, but it remains in the default suite per the
    WS-B-003 Acceptance 2 requirement.
    """
    golden = np.load(_GOLDEN_DIR / "l2_music_sigma_table.npz")
    snrs = golden["snr_db"]
    median_golden = golden["median_claimed_sigma_deg"]

    for snr_db, expected_median in zip(snrs, median_golden, strict=True):
        scenario = conftest._build_l2_uca_scenario(float(snr_db))
        sigmas: list[float] = []
        for trial in range(golden_generator._L2_SIGMA_TABLE_TRIALS):
            sigma = golden_generator.claim_l2_sigma_one_trial(scenario, seed=trial)
            if sigma is not None:
                sigmas.append(sigma)
        recomputed = float(np.median(sigmas))
        rel = abs(recomputed - expected_median) / expected_median
        assert rel <= _GOLDEN_SIGMA_TABLE_REL_TOL, (
            f"SNR={snr_db} dB: median L2 sigma drifted from golden "
            f"{expected_median:.4f} -> {recomputed:.4f} ({rel * 100:.2f} %)."
        )
