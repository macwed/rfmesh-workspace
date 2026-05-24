"""``SyntheticTransmitter`` -- the simulator implementing ``Transmitter`` (ADR-025).

Symmetric to ``SyntheticReceiver``: writes complex baseband IQ into a
shared ``LoopbackChannel`` from which a paired ``LoopbackReceiver``
reads. The pair closes the DSSS simulator-first loop -- two
synthetic nodes exchange spread DSSS frames in pytest with zero
hardware, exactly the model ADR-021 / ADR-025 set out.

ITER 3 SCOPE

* ``SyntheticTransmitter`` (single-channel, the ``Transmitter`` Protocol).
* ``SyntheticTransmitterCapabilities`` in ``simulator.capabilities``.
* Coherent / multi-channel transmit deferred -- ``CoherentTransmitter``
  is reserved in 1.3.0 contracts (see ``protocols.CoherentTransmitter``);
  the ``_CoherentSyntheticTransmitter`` subclass will land alongside the
  first feature that needs transmit beamforming.

CONTRACT GUARANTEES

* ``write(iq)`` returns exactly ``len(iq)`` or raises -- mirror of the
  ``Receiver.read`` "no silent short read" guarantee on the TX side.
  Channel ``write`` itself is non-blocking and cannot drop, so the
  only failure modes here are (a) wrong shape / dtype, (b) lifecycle
  violations.
* ``capabilities()`` returns ``SyntheticTransmitterCapabilities`` with
  ``driver="sim"``, ``n_tx_channels=1``, the loopback's sample rate,
  and ``max_tx_power_normalized=1.0``. The honest no-dBm rule applies
  on TX exactly as on RX (none of the SDRs in scope are
  power-calibrated on transmit).
* ``configure(NodeConfig)`` records the config but does not retune
  the loopback (the loopback's sample rate is authoritative).
"""

from __future__ import annotations

import numpy as np
from rfmesh_contracts import NodeConfig  # type: ignore[import-untyped, unused-ignore]

from ..exceptions import InvalidWriteSizeError, TransmitterNotOpenError
from .capabilities import SyntheticTransmitterCapabilities
from .loopback_channel import LoopbackChannel


class SyntheticTransmitter:
    """Synthetic implementation of ``Transmitter`` for the comms simulator.

    Construct with a shared ``LoopbackChannel`` and call ``open()``
    before ``write(iq)``. The transmitter holds no internal IQ
    state -- it forwards each block straight into the channel after
    validating shape.

    Lifecycle: ``open()`` -> ``configure(NodeConfig)`` -> ``write(iq)``
    repeatedly -> ``close()``. Idempotent ``open``/``close``.
    """

    def __init__(self, channel: LoopbackChannel) -> None:
        self._channel = channel
        self._is_open = False
        self._node_config: NodeConfig | None = None

    @property
    def channel(self) -> LoopbackChannel:
        """The shared loopback channel this transmitter writes to."""
        return self._channel

    def open(self) -> None:
        """Mark the transmitter opened. Idempotent."""
        self._is_open = True

    def configure(self, config: NodeConfig) -> None:
        """Record the node config; the loopback's sample rate stays authoritative.

        Symmetric to ``LoopbackReceiver.configure``: stores the
        config so a future runtime can introspect intended tuning,
        but does not change the channel's wiring.
        """
        self._node_config = config

    def write(self, iq: np.ndarray) -> int:
        """Send ``iq`` through the loopback; return the number of samples written.

        Raises:
            TransmitterNotOpenError: if ``open()`` has not been
                called (or ``close()`` has run since the last
                ``open()``).
            InvalidWriteSizeError: if ``iq`` is empty or not 1-D.
            ValueError: from the underlying channel on any
                impairment-chain failure.
        """
        if not self._is_open:
            msg = "SyntheticTransmitter.write() called before open() (or after close())."
            raise TransmitterNotOpenError(msg)
        if iq.ndim != 1:
            msg = (
                f"SyntheticTransmitter.write(iq) requires 1-D iq "
                f"(got shape {iq.shape})."
            )
            raise InvalidWriteSizeError(msg)
        if iq.size == 0:
            msg = "SyntheticTransmitter.write(iq) requires non-empty iq."
            raise InvalidWriteSizeError(msg)
        return self._channel.write(iq)

    def capabilities(self) -> SyntheticTransmitterCapabilities:
        """Return the capability snapshot (driver = ``sim``, 1 TX channel)."""
        return SyntheticTransmitterCapabilities(
            driver="sim",
            n_tx_channels=1,
            actual_sample_rate_hz=self._channel.sample_rate_hz,
            max_tx_power_normalized=1.0,
        )

    def close(self) -> None:
        """Mark the transmitter closed. Idempotent."""
        self._is_open = False
