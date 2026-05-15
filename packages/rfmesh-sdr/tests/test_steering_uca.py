"""Acceptance criterion 1(f): UCA per-element phase matches the closed-form steering vector."""

from __future__ import annotations

import math

import numpy as np
from rfmesh_sdr import SimulationScenario, SyntheticReceiver

_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_TOLERANCE_DEG = 2.0
_TOLERANCE_RAD = math.radians(_TOLERANCE_DEG)
_DEG_180 = 180.0
_DEG_360 = 360.0


def _wrap_pm180_rad(angle_rad: float) -> float:
    """Wrap an angle in radians to (-pi, pi]."""
    deg = math.degrees(angle_rad)
    wrapped = ((deg + _DEG_180) % _DEG_360) - _DEG_180
    return math.radians(wrapped)


def test_uca_phase_pattern_matches_closed_form(uca_scenario: SimulationScenario) -> None:
    """Per-element phase matches ``-2 pi (r/lambda) cos(theta - alpha_i)`` mod 2 pi.

    8-element UCA at r = 0.5 * lambda for 915 MHz, CW emitter at array-local
    137 deg, 30 dB SNR over n = 32768 samples. Each element i sits at ring
    angle ``alpha_i = 2 pi i / 8`` (alpha_0 = 0 by the convention in
    ``ArraySpec.uca``). Phases are compared *relative to channel 0* -- the
    common-mode emitter phase cancels and the residual difference is the
    steering-vector phase delta between elements i and 0.
    """
    n = 32768
    receiver = SyntheticReceiver(uca_scenario, seed=11)
    receiver.open()
    block = receiver.read_coherent(n)

    emitter = uca_scenario.emitters[0]
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / emitter.frequency_hz
    assert uca_scenario.array is not None
    n_elements = uca_scenario.array.n_elements
    # Reconstruct ring radius and angles from the ArraySpec.
    positions = uca_scenario.array.element_positions_m
    radius_m = float(math.hypot(positions[0, 0], positions[0, 1]))
    alphas = np.array(
        [math.atan2(positions[i, 1], positions[i, 0]) for i in range(n_elements)],
        dtype=np.float64,
    )

    theta_rad = math.radians(emitter.azimuth_deg)  # heading default is 0
    expected_phase_full = -2.0 * math.pi * (radius_m / wavelength_m) * np.cos(theta_rad - alphas)

    # Cross-channel correlation against channel 0 isolates the steering phase
    # delta (the emitter's common-mode phase cancels in the conjugate product).
    for i in range(1, n_elements):
        cross = np.mean(block[i, :] * np.conj(block[0, :]))
        measured_delta = math.atan2(cross.imag, cross.real)
        expected_delta = float(expected_phase_full[i] - expected_phase_full[0])
        residual = _wrap_pm180_rad(measured_delta - expected_delta)
        assert abs(residual) < _TOLERANCE_RAD, (
            f"channel {i} vs 0: measured delta {math.degrees(measured_delta):.3f} deg, "
            f"expected {math.degrees(expected_delta):.3f} deg, "
            f"residual {math.degrees(residual):.3f} deg."
        )
