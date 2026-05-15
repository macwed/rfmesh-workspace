"""Acceptance criterion 1(h): RSSI sweep locates the emitter peak within +/-5 deg.

The test models exactly the L1 estimator's amplitude-sweep loop: rotate
the antenna in 1 deg steps over the full circle, take a block of IQ at
each heading, compute relative RSSI as ``10*log10(mean(|iq|^2))``, and
``argmax`` to find the peak heading.

Implementation note: the RSSI math is *inline* in the test (per
WS-A-001 acceptance criteria), NOT a dependency on the (not-yet-existing)
``rfmesh_dsp`` package -- the SDR workstream must not import from a
sibling workstream (Invariant 2).
"""

from __future__ import annotations

import numpy as np
from rfmesh_sdr import SimulationScenario, SyntheticReceiver

# Acceptance criterion 1(h) numbers, named to satisfy PLR2004 and to keep
# the test's intent obvious at the failure-message level.
_EXPECTED_PEAK_AZIMUTH_DEG = 137
_PEAK_TOLERANCE_DEG = 5
_SWEEP_BLOCK_SAMPLES = 8192
_FULL_CIRCLE_DEG = 360
_HALF_CIRCLE_DEG = 180


def test_yagi_sweep_peak_locates_emitter(peak_test_scenario: SimulationScenario) -> None:
    """Sweep 0..359 deg, find the RSSI peak, verify it is within +/-5 deg of 137 deg."""
    receiver = SyntheticReceiver(peak_test_scenario, seed=42)
    receiver.open()

    headings = np.arange(_FULL_CIRCLE_DEG)
    rssi_db = np.empty(len(headings), dtype=np.float64)

    for i, heading_deg in enumerate(headings):
        receiver.set_antenna_heading(float(heading_deg))
        iq = receiver.read(_SWEEP_BLOCK_SAMPLES)
        rssi_db[i] = 10.0 * np.log10(np.mean(np.abs(iq) ** 2))

    peak_heading_deg = int(headings[int(np.argmax(rssi_db))])
    # Signed shortest-arc difference, accounting for wrap.
    diff_deg = (
        (peak_heading_deg - _EXPECTED_PEAK_AZIMUTH_DEG + _HALF_CIRCLE_DEG) % _FULL_CIRCLE_DEG
    ) - _HALF_CIRCLE_DEG
    assert abs(diff_deg) <= _PEAK_TOLERANCE_DEG, (
        f"RSSI peak at {peak_heading_deg} deg, expected "
        f"~{_EXPECTED_PEAK_AZIMUTH_DEG} deg (signed delta {diff_deg} deg)."
    )
