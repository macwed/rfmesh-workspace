"""Acceptance criterion 1(e): ULA cross-channel phase matches the closed-form steering vector."""

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
    """Wrap an angle in radians to the (-pi, pi] interval for cyclic comparison."""
    deg = math.degrees(angle_rad)
    wrapped = ((deg + _DEG_180) % _DEG_360) - _DEG_180
    return math.radians(wrapped)


def test_ula_phase_pattern_matches_closed_form(ula_scenario: SimulationScenario) -> None:
    """Measured cross-channel phase matches ``-2 pi (d/lambda) sin(theta)``.

    Setup: 4-element ULA at d = lambda/2 for 915 MHz, CW emitter at
    array-local 35 deg, 30 dB SNR over n = 32768 samples. The expected
    inter-element phase difference (consecutive channels) is
    ``-2 pi (d/lambda) sin(35 deg)``; measured Delta-phi within
    +/- 2 degrees.
    """
    n = 32768
    receiver = SyntheticReceiver(ula_scenario, seed=7)
    receiver.open()
    block = receiver.read_coherent(n)

    emitter = ula_scenario.emitters[0]
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / emitter.frequency_hz
    # Pull element spacing from the array spec (consecutive ULA elements
    # differ by spacing_m along y).
    assert ula_scenario.array is not None
    spacing_m = float(
        ula_scenario.array.element_positions_m[1, 1] - ula_scenario.array.element_positions_m[0, 1]
    )
    theta_rad = math.radians(emitter.azimuth_deg)  # heading default is 0
    expected_delta_phi = -2.0 * math.pi * (spacing_m / wavelength_m) * math.sin(theta_rad)
    expected_wrapped = _wrap_pm180_rad(expected_delta_phi)

    # Δφ_{i+1, i} = phase(<x_{i+1} * conj(x_i)>) -- the phase of the
    # cross-channel correlation is exactly the steering-vector phase
    # difference (the constant CW amplitude factor is real-positive).
    for i in range(3):
        cross = np.mean(block[i + 1, :] * np.conj(block[i, :]))
        measured = math.atan2(cross.imag, cross.real)
        delta = _wrap_pm180_rad(measured - expected_wrapped)
        assert abs(delta) < _TOLERANCE_RAD, (
            f"channel pair {i + 1}-{i}: measured {math.degrees(measured):.3f} deg, "
            f"expected {math.degrees(expected_wrapped):.3f} deg, "
            f"delta {math.degrees(delta):.3f} deg."
        )
