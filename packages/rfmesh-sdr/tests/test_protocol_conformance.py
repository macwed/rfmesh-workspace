"""Acceptance criteria 1(a) and 1(b): SyntheticReceiver satisfies the Protocols."""

from __future__ import annotations

from rfmesh_contracts import Receiver  # type: ignore[import-untyped, unused-ignore]
from rfmesh_sdr import SimulationScenario, SyntheticReceiver


def test_synthetic_receiver_is_receiver(default_scenario: SimulationScenario) -> None:
    """The Receiver Protocol is @runtime_checkable; SyntheticReceiver is one."""
    receiver = SyntheticReceiver(default_scenario)
    assert isinstance(receiver, Receiver)


def test_capabilities_shape(default_scenario: SimulationScenario) -> None:
    """Capabilities advertise the simulator's honest defaults."""
    receiver = SyntheticReceiver(default_scenario)
    caps = receiver.capabilities()
    assert caps.driver == "sim"
    assert caps.n_coherent_channels == 1
    assert caps.is_power_calibrated is False
    assert caps.actual_sample_rate_hz == default_scenario.sample_rate_hz
