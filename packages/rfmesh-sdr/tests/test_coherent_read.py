"""Acceptance criteria 1(c) and 1(d): coherent read shape and single-channel parity."""

from __future__ import annotations

import numpy as np
import pytest
from rfmesh_sdr import SimulationScenario, SyntheticReceiver

_ULA_N_CHANNELS = 4
_COHERENT_BLOCK_NDIM = 2


@pytest.mark.parametrize("n", [1024, 8192])
def test_read_coherent_shape_and_dtype(
    ula_scenario: SimulationScenario,
    n: int,
) -> None:
    """``read_coherent(n)`` returns a (n_channels, n) complex64 block."""
    receiver = SyntheticReceiver(ula_scenario, seed=0)
    receiver.open()
    block = receiver.read_coherent(n)
    assert block.dtype == np.complex64
    assert block.shape == (_ULA_N_CHANNELS, n)
    assert block.ndim == _COHERENT_BLOCK_NDIM


def test_read_single_channel_returns_channel_zero(
    ula_scenario: SimulationScenario,
) -> None:
    """On a coherent receiver, read(n) equals read_coherent(n)[0, :] byte-wise at matched seed.

    Two fresh receivers seeded identically: the first calls ``read``, the
    second calls ``read_coherent`` and slices row 0. The arrays must match
    byte-for-byte, proving the coherent ``read`` is row 0 of the same
    RNG-consumption render rather than a separate single-channel path.
    """
    n = 4096
    receiver_a = SyntheticReceiver(ula_scenario, seed=42)
    receiver_a.open()
    iq_1d = receiver_a.read(n)

    receiver_b = SyntheticReceiver(ula_scenario, seed=42)
    receiver_b.open()
    iq_2d = receiver_b.read_coherent(n)

    assert iq_1d.shape == (n,)
    assert iq_1d.dtype == np.complex64
    assert np.array_equal(iq_1d, iq_2d[0, :])
