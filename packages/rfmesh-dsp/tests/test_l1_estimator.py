"""Tests for ``L1AmplitudeSweepEstimator``.

Covers WS-B-001 Acceptance 1 and 2:

* Protocol conformance (Acceptance 1).
* Peak recovery within +/-2 deg at 20 dB SNR.
* ``None`` on a flat / sub-prominence sweep (Invariant 4 surface).
* ``method == Capability.L1_RSSI``.
* The two golden-file checks (Acceptance 2): per-heading RSSI sweep
  matches ``l1_sweep_137deg_20db.npz`` and the 200-trial sigma table
  matches ``l1_sigma_table.npz`` within 5 % relative.
"""

from __future__ import annotations

from pathlib import Path

import conftest  # type: ignore[import-not-found, unused-ignore]
import golden_generator  # type: ignore[import-not-found, unused-ignore]
import numpy as np
import pytest
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    BearingEstimator,
    Capability,
    GeodeticPosition,
)
from rfmesh_dsp import L1AmplitudeSweepEstimator, compute_rssi_dbfs
from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
    SimulationScenario,
    SyntheticReceiver,
)

_NODE_ID = "test-node"
_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)
_T_UNIX_NS = 1_700_000_000_000_000_000
_SWEEP_BLOCK_SAMPLES = 8192
_SWEEP_STEP_DEG = 1.0
_PEAK_AZIMUTH_DEG = 137.0
_PEAK_TOLERANCE_DEG = 2.0
_GOLDEN_DIR = Path(__file__).parent / "golden"
# Per-heading RSSI must match the stored golden to within machine
# precision; bumped above strict 0 to absorb FP rounding from numpy/scipy
# version drift (numpy guarantees deterministic outputs at fixed seeds
# but not bit-identical across micro-versions).
_GOLDEN_SWEEP_TOL_DB = 1e-6
# Relative tolerance for the recomputed median claimed sigma vs the golden
# entry per SNR; comfortably wider than the chi-square MC noise on the
# median of 200 trials at the SNRs covered.
_GOLDEN_SIGMA_TABLE_REL_TOL = 0.05


def _build_estimator() -> L1AmplitudeSweepEstimator:
    return L1AmplitudeSweepEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        sweep_step_deg=_SWEEP_STEP_DEG,
        sweep_dwell_samples=_SWEEP_BLOCK_SAMPLES,
    )


def _drive_full_sweep(
    estimator: L1AmplitudeSweepEstimator,
    receiver: SyntheticReceiver,
) -> None:
    """Drive a 0..359 deg, 1 deg-step sweep through observe()."""
    estimator.begin_sweep(t_unix_ns=_T_UNIX_NS)
    for h in np.arange(0.0, 360.0, _SWEEP_STEP_DEG):
        receiver.set_antenna_heading(float(h))
        iq = receiver.read(_SWEEP_BLOCK_SAMPLES)
        estimator.observe(float(h), iq)


def test_protocol_conformance() -> None:
    """``BearingEstimator`` is ``@runtime_checkable``; the L1 class is one."""
    estimator = _build_estimator()
    assert isinstance(estimator, BearingEstimator)


def test_method_is_l1_rssi() -> None:
    """``method`` advertises L1 so the node runtime can route by capability."""
    estimator = _build_estimator()
    assert estimator.method is Capability.L1_RSSI


def test_peak_recovers_known_angle(peak_test_scenario: SimulationScenario) -> None:
    """At 20 dB SNR a single sweep recovers 137 deg within +/-2 deg."""
    receiver = SyntheticReceiver(peak_test_scenario, seed=0)
    receiver.open()
    estimator = _build_estimator()
    _drive_full_sweep(estimator, receiver)
    report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    receiver.close()

    assert report is not None
    assert report.method is Capability.L1_RSSI
    assert report.node_id == _NODE_ID
    assert report.t_unix_ns == _T_UNIX_NS
    # Signed shortest-arc deviation handles the 0/360 seam correctly.
    diff = ((report.azimuth_deg - _PEAK_AZIMUTH_DEG + 180.0) % 360.0) - 180.0
    assert abs(diff) <= _PEAK_TOLERANCE_DEG, (
        f"recovered azimuth {report.azimuth_deg:.3f} deg differs from "
        f"{_PEAK_AZIMUTH_DEG} deg by {diff:+.3f} deg (> {_PEAK_TOLERANCE_DEG} deg)."
    )
    assert report.azimuth_sigma_deg > 0.0


def test_returns_none_on_flat_sweep(flat_scenario: SimulationScenario) -> None:
    """An emitter ~30 dB below the floor leaves no usable peak; ``estimate`` returns ``None``."""
    receiver = SyntheticReceiver(flat_scenario, seed=0)
    receiver.open()
    estimator = _build_estimator()
    _drive_full_sweep(estimator, receiver)
    report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    receiver.close()
    assert report is None


def test_returns_none_when_no_begin_sweep() -> None:
    """Calling ``estimate`` without a prior ``begin_sweep`` returns ``None`` honestly."""
    estimator = _build_estimator()
    report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    assert report is None


def test_observe_rejects_wrong_block_size(peak_test_scenario: SimulationScenario) -> None:
    """An L1-aware runtime must hand consistent dwell sizes; mismatches are a wiring bug."""
    receiver = SyntheticReceiver(peak_test_scenario, seed=0)
    receiver.open()
    estimator = _build_estimator()
    estimator.begin_sweep(t_unix_ns=_T_UNIX_NS)
    receiver.set_antenna_heading(0.0)
    iq_short = receiver.read(_SWEEP_BLOCK_SAMPLES // 2)
    with pytest.raises(ValueError, match="sweep_dwell_samples"):
        estimator.observe(0.0, iq_short)
    receiver.close()


def test_estimate_re_arms_for_next_sweep(
    peak_test_scenario: SimulationScenario,
    flat_scenario: SimulationScenario,
) -> None:
    """After ``estimate`` (success or fail), the instance is ready for the next sweep."""
    estimator = _build_estimator()

    # First sweep: usable -> emits a report and clears state.
    rx_peak = SyntheticReceiver(peak_test_scenario, seed=0)
    rx_peak.open()
    _drive_full_sweep(estimator, rx_peak)
    report_a = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    rx_peak.close()
    assert report_a is not None

    # Second sweep: flat -> returns None but accumulator must already be clean
    # (otherwise the first sweep's data would leak into the second's argmax).
    rx_flat = SyntheticReceiver(flat_scenario, seed=0)
    rx_flat.open()
    _drive_full_sweep(estimator, rx_flat)
    report_b = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    rx_flat.close()
    assert report_b is None


def test_golden_sweep_rssi(peak_test_scenario: SimulationScenario) -> None:
    """Per-heading RSSI sweep at seed=42 matches the committed golden array.

    The simulator and ``compute_rssi_dbfs`` are deterministic; this guards
    against silent numerical drift in either component (Invariant 3 -- the
    golden-file gate for the dsp workstream).
    """
    golden = np.load(_GOLDEN_DIR / "l1_sweep_137deg_20db.npz")
    headings_golden = golden["headings_deg"]
    rssi_golden = golden["rssi_dbfs"]

    receiver = SyntheticReceiver(peak_test_scenario, seed=42)
    receiver.open()
    rssi_now = np.empty(headings_golden.size, dtype=np.float64)
    for i, h in enumerate(headings_golden):
        receiver.set_antenna_heading(float(h))
        iq = receiver.read(_SWEEP_BLOCK_SAMPLES)
        rssi_now[i] = compute_rssi_dbfs(iq)
    receiver.close()

    max_abs_diff = float(np.max(np.abs(rssi_now - rssi_golden)))
    assert max_abs_diff <= _GOLDEN_SWEEP_TOL_DB, (
        f"sweep RSSI drifted from golden by max-abs-diff {max_abs_diff:.3e} dB."
    )


@pytest.mark.slow
def test_golden_sigma_table() -> None:
    """Recompute the 200-trial median claimed sigma per SNR; check within 5 % of golden.

    Slow (~80 s). Marked ``slow`` so a future targeted run can skip with
    ``-m 'not slow'``, but it remains in the default suite per the WS-B-001
    Acceptance 2 requirement.
    """
    golden = np.load(_GOLDEN_DIR / "l1_sigma_table.npz")
    snrs = golden["snr_db"]
    median_golden = golden["median_claimed_sigma_deg"]

    for snr_db, expected_median in zip(snrs, median_golden, strict=True):
        scenario = conftest._build_peak_scenario(float(snr_db))
        sigmas: list[float] = []
        for trial in range(golden_generator._SIGMA_TABLE_TRIALS):
            sigma = golden_generator.claim_sigma_one_trial(scenario, seed=trial)
            if sigma is not None:
                sigmas.append(sigma)
        recomputed = float(np.median(sigmas))
        rel = abs(recomputed - expected_median) / expected_median
        assert rel <= _GOLDEN_SIGMA_TABLE_REL_TOL, (
            f"SNR={snr_db} dB: median sigma drifted from golden "
            f"{expected_median:.4f} -> {recomputed:.4f} ({rel * 100:.2f} %)."
        )


# ---------------------------------------------------------------------------
# E1 — last_refusal_reason surfaces honest diagnostic when estimate returns None
# ---------------------------------------------------------------------------


def test_last_refusal_reason_none_before_first_estimate() -> None:
    """Fresh estimator has no refusal reason yet."""
    estimator = _build_estimator()
    assert estimator.last_refusal_reason is None


def test_last_refusal_reason_set_when_no_begin_sweep() -> None:
    """Calling estimate without begin_sweep gives a useful diagnostic."""
    estimator = _build_estimator()
    report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    assert report is None
    reason = estimator.last_refusal_reason
    assert reason is not None
    assert "underpopulated" in reason or "begin_sweep" in reason


def test_last_refusal_reason_cleared_on_successful_estimate(
    peak_test_scenario: SimulationScenario,
) -> None:
    """A clean recovery clears any stale refusal text from a prior sweep."""
    receiver = SyntheticReceiver(peak_test_scenario, seed=0)
    receiver.open()
    estimator = _build_estimator()
    # First call without begin_sweep -> sets a refusal reason.
    estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    assert estimator.last_refusal_reason is not None
    # Now a clean sweep.
    _drive_full_sweep(estimator, receiver)
    report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    receiver.close()
    assert report is not None
    assert estimator.last_refusal_reason is None


def test_last_refusal_reason_prominence_gate_message() -> None:
    """Below-prominence refusal includes a 'multipath dominance' hint string.

    Build a flat-RSSI sweep (constant 0.0 dB headings) — peak/floor delta = 0 dB,
    well below the 6 dB gate. The refusal reason must name the prominence gate
    so the operator (or future status_detail surface) understands why.
    """
    estimator = L1AmplitudeSweepEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        sweep_step_deg=_SWEEP_STEP_DEG,
        sweep_dwell_samples=_SWEEP_BLOCK_SAMPLES,
    )
    estimator.begin_sweep(t_unix_ns=_T_UNIX_NS)
    # Inject a flat sweep: 16 headings, all-zero IQ -> compute_rssi_dbfs
    # yields a finite floor with no peak. Use a small dwell to avoid
    # allocating a giant zeros() block needlessly.
    flat_iq = np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64)
    flat_iq[:] = 1e-6 + 0j  # tiny constant so RSSI is finite, not -inf
    for h in np.arange(0.0, 360.0, _SWEEP_STEP_DEG):
        estimator.observe(float(h), flat_iq)
    report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    assert report is None
    reason = estimator.last_refusal_reason
    assert reason is not None
    assert "prominence" in reason.lower()
    assert "multipath" in reason.lower()  # operator-facing hint
