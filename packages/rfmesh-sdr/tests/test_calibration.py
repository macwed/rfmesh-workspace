"""Acceptance criteria 1(i)-1(l): calibration lifecycle, recovery, uncalibrated state, SNR floor."""

from __future__ import annotations

import math

import numpy as np
import pytest
from rfmesh_contracts import NodeConfig  # type: ignore[import-untyped, unused-ignore]
from rfmesh_sdr import (
    CalibrationFailedError,
    SimulationScenario,
    SyntheticReceiver,
)

_AMPLITUDE_TOL_DB = 0.1
_PHASE_TOL_DEG = 1.0


def test_is_calibrated_lifecycle(
    ula_scenario: SimulationScenario,
    simulator_node_config: NodeConfig,
) -> None:
    """is_calibrated: False after construction, True after calibrate(), False after configure()."""
    receiver = SyntheticReceiver(ula_scenario, seed=5)
    # Property exists on coherent instances even before open().
    assert receiver.is_calibrated is False
    receiver.open()
    assert receiver.is_calibrated is False
    receiver.calibrate()
    assert receiver.is_calibrated is True
    receiver.configure(simulator_node_config)
    assert receiver.is_calibrated is False


def test_calibrate_recovers_injected_offsets(
    impaired_ula_scenario: SimulationScenario,
) -> None:
    """After calibrate(), inter-channel ratios at boresight are unity within 0.1 dB / 1 deg.

    The fixture injects per-channel complex offsets between the noise source
    and the ADC. Calibration measures and inverts them; subsequent
    read_coherent renders signal that, after the per-channel correction
    pipeline, is phase- and gain-aligned across channels.
    """
    n = 32768
    receiver = SyntheticReceiver(impaired_ula_scenario, seed=17)
    receiver.open()
    receiver.calibrate()
    assert receiver.is_calibrated is True

    block = receiver.read_coherent(n)
    # Ratio with channel 0 -- 1+0j on every channel pair if calibration is good.
    ref_power = float(np.mean(np.abs(block[0, :]) ** 2))
    for i in range(1, block.shape[0]):
        cross = np.mean(block[i, :] * np.conj(block[0, :]))
        ratio = cross / ref_power
        amplitude = abs(ratio)
        amplitude_db = 20.0 * math.log10(amplitude) if amplitude > 0 else float("-inf")
        phase_deg = math.degrees(math.atan2(ratio.imag, ratio.real))
        assert abs(amplitude_db) < _AMPLITUDE_TOL_DB, (
            f"channel {i}: amplitude ratio {amplitude:.5f} ({amplitude_db:.4f} dB) "
            f"outside +/- {_AMPLITUDE_TOL_DB} dB."
        )
        assert abs(phase_deg) < _PHASE_TOL_DEG, (
            f"channel {i}: phase {phase_deg:.4f} deg outside +/- {_PHASE_TOL_DEG} deg."
        )


def test_uncalibrated_read_is_allowed_but_flagged(
    impaired_ula_scenario: SimulationScenario,
) -> None:
    """read_coherent on an uncalibrated receiver returns IQ without raising.

    The contract is "label state honestly, leave the refuse-to-emit decision
    to the L2 estimator" -- the simulator does not silently calibrate or
    refuse to render.
    """
    receiver = SyntheticReceiver(impaired_ula_scenario, seed=23)
    receiver.open()
    assert receiver.is_calibrated is False
    block = receiver.read_coherent(1024)
    assert block.shape == (4, 1024)
    assert receiver.is_calibrated is False


def test_calibrate_insufficient_snr_raises(
    low_snr_calibration_ula_scenario: SimulationScenario,
) -> None:
    """A scenario with reference SNR below the noise floor refuses to calibrate."""
    receiver = SyntheticReceiver(low_snr_calibration_ula_scenario, seed=31)
    receiver.open()
    with pytest.raises(CalibrationFailedError):
        receiver.calibrate()
    assert receiver.is_calibrated is False
