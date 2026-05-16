"""Acceptance criterion 1(h): the WS-A-001/002 baseline render path is unchanged.

The most important test in WS-A-003. The new ``channel`` and
``receiver_impairments`` plug-ins must default to behaviour that produces
**byte-identical** IQ to the pre-ticket render path. A scenario constructed
without any of the new optional fields must:

* Run through the same render order as before (per-emitter base * antenna
  * Friis * steering, plus AWGN, plus per-channel offsets in coherent mode).
* Consume the same per-stream RNG state as before (no spurious draws from
  the noise or cal-noise streams just because the new impairment-stream
  ``Generator`` exists).
* Produce a buffer that ``np.array_equal``\\ s the explicit-defaults
  construction. If this test fails, the regression anchor has done its
  job; surface and stop (do not paper over).
"""

from __future__ import annotations

import numpy as np
from rfmesh_sdr import (
    FreeSpaceChannel,
    IdentityReceiverImpairments,
    SimulationScenario,
    SyntheticReceiver,
)

_BLOCK_SAMPLES = 4096
_SEED = 42


def _explicit_defaults_scenario(source: SimulationScenario) -> SimulationScenario:
    """Construct a new scenario equal to ``source`` but with both new fields explicit."""
    return SimulationScenario(
        emitters=source.emitters,
        antenna=source.antenna,
        sample_rate_hz=source.sample_rate_hz,
        center_freq_hz=source.center_freq_hz,
        noise_floor_dbfs=source.noise_floor_dbfs,
        channel=FreeSpaceChannel(),
        array=source.array,
        impairments=source.impairments,
        calibration_reference_snr_db=source.calibration_reference_snr_db,
        receiver_impairments=IdentityReceiverImpairments(),
    )


def test_default_scenario_unchanged(default_scenario: SimulationScenario) -> None:
    """Single-channel default scenario produces byte-identical IQ to explicit defaults.

    No impairment defaults to "free-space-only Friis + AWGN". A scenario
    constructed without any of the new optional fields must produce IQ
    that ``np.array_equal``\\ s the explicit-defaults construction at the
    same seed and call sequence.
    """
    explicit = _explicit_defaults_scenario(default_scenario)

    receiver_default = SyntheticReceiver(default_scenario, seed=_SEED)
    receiver_explicit = SyntheticReceiver(explicit, seed=_SEED)
    receiver_default.open()
    receiver_explicit.open()

    iq_default = receiver_default.read(_BLOCK_SAMPLES)
    iq_explicit = receiver_explicit.read(_BLOCK_SAMPLES)

    assert iq_default.shape == iq_explicit.shape
    assert iq_default.dtype == iq_explicit.dtype
    assert np.array_equal(iq_default, iq_explicit), (
        "Default scenario IQ diverges from explicit-defaults construction. "
        "Either the channel default or the receiver_impairments default changed "
        "observable behaviour; this is the WS-A-001/002 regression anchor."
    )


def test_default_coherent_scenario_unchanged(ula_scenario: SimulationScenario) -> None:
    """Coherent default scenario also produces byte-identical IQ to explicit defaults.

    Catches a regression in the coherent render path where the WS-A-002
    array/impairments paths might silently start consuming from a new
    RNG stream or apply the impairments in a different order.
    """
    explicit = _explicit_defaults_scenario(ula_scenario)

    receiver_default = SyntheticReceiver(ula_scenario, seed=_SEED)
    receiver_explicit = SyntheticReceiver(explicit, seed=_SEED)
    receiver_default.open()
    receiver_explicit.open()

    iq_default = receiver_default.read_coherent(_BLOCK_SAMPLES)
    iq_explicit = receiver_explicit.read_coherent(_BLOCK_SAMPLES)

    assert iq_default.shape == iq_explicit.shape
    assert iq_default.dtype == iq_explicit.dtype
    assert np.array_equal(iq_default, iq_explicit), (
        "Coherent default scenario IQ diverges from explicit-defaults construction."
    )
