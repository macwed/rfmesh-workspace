"""Acceptance criterion 1(g): CUSTOM layout phase matches the element-by-element steering vector."""

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


def test_custom_layout_matches_closed_form(custom_scenario: SimulationScenario) -> None:
    """Per-element phase from a non-symmetric CUSTOM layout matches the closed-form steering.

    Layout: ``(0,0), (0.1,0), (0,0.15), (0.13,0.07)`` metres at 915 MHz,
    emitter at array-local 50 deg, 30 dB SNR. The expected per-element
    phase at channel i (relative to a virtual origin reference, which is
    channel 0's position) is ``-2 pi dot(p_i, k_hat) / lambda`` with
    ``k_hat = (cos delta, sin delta)``.
    """
    n = 32768
    receiver = SyntheticReceiver(custom_scenario, seed=3)
    receiver.open()
    block = receiver.read_coherent(n)

    emitter = custom_scenario.emitters[0]
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / emitter.frequency_hz
    assert custom_scenario.array is not None
    positions = custom_scenario.array.element_positions_m
    n_elements = custom_scenario.array.n_elements

    delta_rad = math.radians(emitter.azimuth_deg)
    k_hat = np.array([math.cos(delta_rad), math.sin(delta_rad)], dtype=np.float64)
    path_delta = positions @ k_hat
    expected_phase_full = -2.0 * math.pi * path_delta / wavelength_m

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
