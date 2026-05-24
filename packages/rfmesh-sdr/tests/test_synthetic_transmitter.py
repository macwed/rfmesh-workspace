"""Tests for ``SyntheticTransmitter`` (ADR-025 Iter 3)."""

from __future__ import annotations

import numpy as np
import pytest
from rfmesh_contracts import Transmitter
from rfmesh_sdr.exceptions import InvalidWriteSizeError, TransmitterNotOpenError
from rfmesh_sdr.simulator import (
    LoopbackChannel,
    SyntheticTransmitter,
    SyntheticTransmitterCapabilities,
)


@pytest.fixture
def channel() -> LoopbackChannel:
    """Lossless 1 MHz loopback for the lifecycle / protocol tests."""
    return LoopbackChannel(sample_rate_hz=1_000_000.0)


@pytest.fixture
def tx(channel: LoopbackChannel) -> SyntheticTransmitter:
    """Closed (not yet opened) transmitter on the lossless channel."""
    return SyntheticTransmitter(channel)


def test_satisfies_transmitter_protocol(tx: SyntheticTransmitter) -> None:
    """Structural conformance to the frozen ``Transmitter`` Protocol."""
    assert isinstance(tx, Transmitter)


def test_capabilities_shape(tx: SyntheticTransmitter) -> None:
    """``capabilities()`` returns the four fields the Protocol requires.

    ``TransmitterCapabilities`` is a structural Protocol but not
    ``@runtime_checkable`` (mirror of ``ReceiverCapabilities`` --
    callers consult the field surface, not ``isinstance``). The
    test asserts every documented field is present with the right
    type and the expected value for a default loopback.
    """
    caps = tx.capabilities()
    assert isinstance(caps, SyntheticTransmitterCapabilities)
    assert caps.driver == "sim"
    assert caps.n_tx_channels == 1
    assert caps.actual_sample_rate_hz == 1_000_000.0
    assert caps.max_tx_power_normalized == 1.0


def test_write_before_open_raises(tx: SyntheticTransmitter) -> None:
    """Lifecycle violation surfaces loudly (B3 mirror of RX side)."""
    iq = np.zeros(8, dtype=np.complex64)
    with pytest.raises(TransmitterNotOpenError):
        tx.write(iq)


def test_write_after_close_raises(tx: SyntheticTransmitter) -> None:
    """Close invalidates write authority; reopen restores it."""
    tx.open()
    tx.write(np.zeros(4, dtype=np.complex64))
    tx.close()
    with pytest.raises(TransmitterNotOpenError):
        tx.write(np.zeros(4, dtype=np.complex64))


def test_write_returns_exact_count(tx: SyntheticTransmitter) -> None:
    """``write(iq)`` returns ``len(iq)`` -- no silent short writes."""
    tx.open()
    iq = np.arange(1024, dtype=np.complex64) + 1j * np.arange(1024, dtype=np.complex64)
    assert tx.write(iq) == iq.size


def test_write_empty_raises(tx: SyntheticTransmitter) -> None:
    """Zero-length input is a programming bug; refuses loudly."""
    tx.open()
    with pytest.raises(InvalidWriteSizeError, match="non-empty"):
        tx.write(np.zeros(0, dtype=np.complex64))


def test_write_multi_dim_raises(tx: SyntheticTransmitter) -> None:
    """2-D / N-D input is a shape bug; refuses loudly."""
    tx.open()
    with pytest.raises(InvalidWriteSizeError, match="1-D"):
        tx.write(np.zeros((2, 8), dtype=np.complex64))


def test_open_close_idempotent(tx: SyntheticTransmitter) -> None:
    """Repeated open / close calls are harmless (lifecycle robustness)."""
    tx.open()
    tx.open()
    tx.close()
    tx.close()
    # Reopen still works.
    tx.open()
    tx.write(np.zeros(4, dtype=np.complex64))


def test_channel_property_exposes_loopback(
    tx: SyntheticTransmitter, channel: LoopbackChannel
) -> None:
    """``tx.channel is channel`` -- shared mutable pipe by design."""
    assert tx.channel is channel
