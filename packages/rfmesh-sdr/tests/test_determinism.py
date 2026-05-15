"""Acceptance criteria 1(e) and 1(f): deterministic reproduction and reseed behaviour."""

from __future__ import annotations

import math

import numpy as np
from rfmesh_sdr import SimulationScenario, SyntheticReceiver


def test_same_seed_same_iq(default_scenario: SimulationScenario) -> None:
    """Two receivers seeded identically produce byte-identical IQ for matched reads."""
    receiver_a = SyntheticReceiver(default_scenario, seed=42)
    receiver_b = SyntheticReceiver(default_scenario, seed=42)
    receiver_a.open()
    receiver_b.open()

    for n in (256, 1024, 4096):
        iq_a = receiver_a.read(n)
        iq_b = receiver_b.read(n)
        # `==` on complex64 is bitwise equality for finite, identically-computed
        # buffers; `array_equal` is the standard NumPy idiom.
        assert np.array_equal(iq_a, iq_b)


def test_reseed_changes_noise_keeps_signal(default_scenario: SimulationScenario) -> None:
    """Reseeding perturbs the noise realisation while preserving the signal mean.

    The default emitter sits at the centre frequency (f_offset = 0), so its
    complex amplitude is constant across samples and the empirical mean of
    the IQ block converges to that constant as n -> infinity. After
    reseeding the noise mean is still ~0; the signal mean is unchanged.
    """
    receiver = SyntheticReceiver(default_scenario, seed=1)
    receiver.open()

    n = 16384
    iq_seed_a = receiver.read(n)

    receiver.reseed(2)
    iq_seed_b = receiver.read(n)

    # The two blocks must be different (noise differs).
    assert not np.array_equal(iq_seed_a, iq_seed_b)

    # Construct the ground-truth deterministic emitter complex amplitude at
    # boresight from the scenario. With current heading 0 and emitter
    # azimuth 0, gain_linear = 1 and the antenna_gain factor is sqrt(1) = 1.
    emitter = default_scenario.emitters[0]
    speed_of_light_m_per_s = 299_792_458.0
    wavelength_m = speed_of_light_m_per_s / emitter.frequency_hz
    free_space_amplitude = wavelength_m / (4.0 * math.pi * emitter.range_m)
    tx_amplitude = 10.0 ** (emitter.tx_power_db / 20.0)
    antenna_gain_linear = default_scenario.antenna.gain_linear(emitter.azimuth_deg)
    expected_amplitude = (
        tx_amplitude
        * math.sqrt(antenna_gain_linear)
        * free_space_amplitude
        * np.exp(1j * math.radians(emitter.phase_deg))
    )

    mean_a = complex(np.mean(iq_seed_a))
    mean_b = complex(np.mean(iq_seed_b))

    # Noise standard deviation at -100 dBFS is 1e-5; std of mean over n
    # samples is ~1e-5 / sqrt(n) ~ 8e-8 at n=16384. Expected signal mean
    # is ~1e-4. A tolerance of 5e-6 is well above the noise-mean floor
    # and well below the signal magnitude -- a robust check.
    tolerance = 5e-6
    assert abs(mean_a - expected_amplitude) < tolerance
    assert abs(mean_b - expected_amplitude) < tolerance
