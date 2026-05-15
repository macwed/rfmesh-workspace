"""Acceptance criterion 1(h): ULA front/back ambiguity vs UCA un-ambiguity, both via simulation.

The simulator must produce the ambiguity behaviour both arrays *physically*
exhibit -- not declare it via documentation. ULA has a mirror plane along
its boresight: an emitter at theta and at (180 deg - theta) gives the same
inter-element phase pattern. UCA has no such mirror plane. The test asserts
both simultaneously from the rendered IQ, so a future implementation that
breaks either property fails loudly here.
"""

from __future__ import annotations

import math

import numpy as np
from rfmesh_sdr import (
    AntennaPattern,
    ArraySpec,
    EmitterSpec,
    SimulationScenario,
    SyntheticReceiver,
)

_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_CENTER_FREQ_HZ = 915e6
_SAMPLE_RATE_HZ = 2_048_000.0
_NOISE_FLOOR_DBFS = -100.0
_RANGE_M = 1000.0
_TARGET_SNR_DB = 30.0
_TEST_THETA_DEG = 35.0
_DEG_180 = 180.0
_DEG_360 = 360.0
_AMBIGUITY_TOLERANCE_DEG = 2.0
_DISTINCT_PATTERN_THRESHOLD_DEG = 10.0


def _wrap_pm180_rad(angle_rad: float) -> float:
    deg = math.degrees(angle_rad)
    wrapped = ((deg + _DEG_180) % _DEG_360) - _DEG_180
    return math.radians(wrapped)


def _tx_power_db_for_snr(distance_m: float, frequency_hz: float) -> float:
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
    pl_amplitude = wavelength_m / (4.0 * math.pi * distance_m)
    pl_power_db = 20.0 * math.log10(pl_amplitude)
    return _TARGET_SNR_DB + _NOISE_FLOOR_DBFS - pl_power_db


def _scenario_at_azimuth(array: ArraySpec, azimuth_deg: float) -> SimulationScenario:
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=azimuth_deg,
                range_m=_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=_tx_power_db_for_snr(_RANGE_M, _CENTER_FREQ_HZ),
            ),
        ),
        antenna=AntennaPattern(hpbw_deg=180.0, back_lobe_floor_db=0.0),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=array,
    )


def _cross_channel_phases(block: np.ndarray) -> np.ndarray:
    """Phases of cross-channel correlations with channel 0, length n_channels (channel 0 is 0)."""
    n_channels = block.shape[0]
    phases = np.zeros(n_channels, dtype=np.float64)
    for i in range(1, n_channels):
        cross = np.mean(block[i, :] * np.conj(block[0, :]))
        phases[i] = math.atan2(cross.imag, cross.real)
    return phases


def test_ula_front_back_ambiguity_and_uca_unambiguity() -> None:
    """ULA: phase pattern at theta == phase pattern at (180-theta). UCA: distinguishable."""
    n = 32768
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / _CENTER_FREQ_HZ
    spacing_m = wavelength_m / 2.0

    theta_a = _TEST_THETA_DEG
    theta_b = 180.0 - _TEST_THETA_DEG

    # --- ULA ---
    ula = ArraySpec.ula(n_elements=4, spacing_m=spacing_m)
    rx_ula_a = SyntheticReceiver(_scenario_at_azimuth(ula, theta_a), seed=21)
    rx_ula_a.open()
    block_ula_a = rx_ula_a.read_coherent(n)
    rx_ula_b = SyntheticReceiver(_scenario_at_azimuth(ula, theta_b), seed=21)
    rx_ula_b.open()
    block_ula_b = rx_ula_b.read_coherent(n)

    phases_ula_a = _cross_channel_phases(block_ula_a)
    phases_ula_b = _cross_channel_phases(block_ula_b)
    # Each per-channel residual should fall well within +/- 2 deg.
    for i in range(1, len(phases_ula_a)):
        residual = _wrap_pm180_rad(phases_ula_a[i] - phases_ula_b[i])
        assert abs(math.degrees(residual)) < _AMBIGUITY_TOLERANCE_DEG, (
            f"ULA: front/back not ambiguous at channel {i}: "
            f"phase_a {math.degrees(phases_ula_a[i]):.3f} deg vs "
            f"phase_b {math.degrees(phases_ula_b[i]):.3f} deg "
            f"(residual {math.degrees(residual):.3f} deg)."
        )

    # --- UCA ---
    uca = ArraySpec.uca(n_elements=8, radius_m=spacing_m)
    rx_uca_a = SyntheticReceiver(_scenario_at_azimuth(uca, theta_a), seed=22)
    rx_uca_a.open()
    block_uca_a = rx_uca_a.read_coherent(n)
    rx_uca_b = SyntheticReceiver(_scenario_at_azimuth(uca, theta_b), seed=22)
    rx_uca_b.open()
    block_uca_b = rx_uca_b.read_coherent(n)

    phases_uca_a = _cross_channel_phases(block_uca_a)
    phases_uca_b = _cross_channel_phases(block_uca_b)
    # At least one channel must show a clearly distinct phase between the two
    # scenarios -- the UCA un-ambiguity guarantee.
    max_distinct_deg = max(
        abs(math.degrees(_wrap_pm180_rad(phases_uca_a[i] - phases_uca_b[i])))
        for i in range(1, len(phases_uca_a))
    )
    assert max_distinct_deg > _DISTINCT_PATTERN_THRESHOLD_DEG, (
        "UCA: front/back patterns indistinguishable -- "
        f"max channel residual {max_distinct_deg:.3f} deg "
        f"(threshold {_DISTINCT_PATTERN_THRESHOLD_DEG} deg)."
    )
