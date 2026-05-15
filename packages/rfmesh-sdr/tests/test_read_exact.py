"""Acceptance criteria 1(c) and 1(d): read returns exactly n samples, n=0 raises."""

from __future__ import annotations

import numpy as np
import pytest
from rfmesh_sdr import (
    InvalidReadSizeError,
    SimulationScenario,
    SyntheticReceiver,
)


@pytest.mark.parametrize("n", [1, 1024, 65536])
def test_read_returns_exact_n_samples(
    default_scenario: SimulationScenario,
    n: int,
) -> None:
    """``read(n)`` returns a 1-D complex64 array of shape ``(n,)``."""
    receiver = SyntheticReceiver(default_scenario, seed=0)
    receiver.open()
    iq = receiver.read(n)
    assert iq.dtype == np.complex64
    assert iq.shape == (n,)
    assert iq.ndim == 1


def test_read_zero_raises(default_scenario: SimulationScenario) -> None:
    """Asking for zero samples is a programming error; raise loudly."""
    receiver = SyntheticReceiver(default_scenario, seed=0)
    receiver.open()
    with pytest.raises(InvalidReadSizeError):
        receiver.read(0)


def test_read_negative_raises(default_scenario: SimulationScenario) -> None:
    """Negative sample counts are likewise invalid."""
    receiver = SyntheticReceiver(default_scenario, seed=0)
    receiver.open()
    with pytest.raises(InvalidReadSizeError):
        receiver.read(-1)
