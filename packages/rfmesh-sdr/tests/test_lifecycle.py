"""Acceptance criterion 1(g): full lifecycle and close-idempotency."""

from __future__ import annotations

import pytest
from rfmesh_contracts import NodeConfig  # type: ignore[import-untyped, unused-ignore]
from rfmesh_sdr import (
    ReceiverNotOpenError,
    SimulationScenario,
    SyntheticReceiver,
)


def test_open_configure_read_close(
    default_scenario: SimulationScenario,
    simulator_node_config: NodeConfig,
) -> None:
    """Drive the full lifecycle; verify close() is idempotent."""
    receiver = SyntheticReceiver(default_scenario, seed=7)
    receiver.open()
    receiver.configure(simulator_node_config)

    for _ in range(3):
        iq = receiver.read(2048)
        assert iq.shape == (2048,)

    receiver.close()
    # Idempotent close.
    receiver.close()


def test_read_before_open_raises(default_scenario: SimulationScenario) -> None:
    """Reading before open() is a lifecycle bug -- raise, never silently return."""
    receiver = SyntheticReceiver(default_scenario)
    with pytest.raises(ReceiverNotOpenError):
        receiver.read(1024)


def test_read_after_close_raises(default_scenario: SimulationScenario) -> None:
    """Reading after close() is the same lifecycle bug as reading before open()."""
    receiver = SyntheticReceiver(default_scenario)
    receiver.open()
    receiver.read(64)
    receiver.close()
    with pytest.raises(ReceiverNotOpenError):
        receiver.read(64)
