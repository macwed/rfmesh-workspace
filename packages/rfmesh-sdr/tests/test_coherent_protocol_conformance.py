"""Acceptance criteria 1(a) and 1(b): SyntheticReceiver satisfies CoherentReceiver iff armed."""

from __future__ import annotations

from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    CoherentReceiver,
    Receiver,
)
from rfmesh_sdr import SimulationScenario, SyntheticReceiver

_EXPECTED_ULA_CHANNELS = 4
_EXPECTED_L1_CHANNELS = 1


def test_synthetic_receiver_is_coherent_receiver(ula_scenario: SimulationScenario) -> None:
    """A scenario with an ArraySpec produces a CoherentReceiver-compatible instance.

    ``CoherentReceiver`` extends ``Receiver`` -- the same instance must also
    satisfy the ``Receiver`` Protocol so an L2-capable node can still take an
    L1-style ``read``.
    """
    receiver = SyntheticReceiver(ula_scenario)
    assert isinstance(receiver, Receiver)
    assert isinstance(receiver, CoherentReceiver)


def test_l1_capability_preserved(default_scenario: SimulationScenario) -> None:
    """A scenario without an ArraySpec produces a single-channel-only receiver.

    ``isinstance(..., Receiver)`` stays True; ``isinstance(..., CoherentReceiver)``
    is False because the coherent methods are not defined on the base class.
    Capabilities report ``n_coherent_channels == 1`` -- the WS-A-001 behaviour.
    """
    receiver = SyntheticReceiver(default_scenario)
    assert isinstance(receiver, Receiver)
    assert not isinstance(receiver, CoherentReceiver)
    caps = receiver.capabilities()
    assert caps.n_coherent_channels == _EXPECTED_L1_CHANNELS


def test_coherent_capabilities_report_array_size(ula_scenario: SimulationScenario) -> None:
    """A 4-element ULA reports n_coherent_channels == 4."""
    receiver = SyntheticReceiver(ula_scenario)
    caps = receiver.capabilities()
    assert caps.n_coherent_channels == _EXPECTED_ULA_CHANNELS
    assert caps.driver == "sim"
    assert caps.is_power_calibrated is False
