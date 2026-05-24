"""In-process TX -> RX channel + a Receiver that reads from it (ADR-025 Iter 3).

The DSSS comms simulator needs the *output* of a ``Transmitter`` to
become the *input* of a ``Receiver`` without involving the OS / a
radio. ``LoopbackChannel`` is that in-process pipe: the
``SyntheticTransmitter`` writes complex IQ chips into it (after the
channel's optional impairments are applied) and a ``LoopbackReceiver``
reads them back. The pair closes the simulator-first loop for comms
the same way ``SyntheticReceiver`` + the scenario emitter pool closes
it for direction-finding.

Why a separate ``LoopbackReceiver`` rather than re-using
``SyntheticReceiver``: the existing receiver synthesises IQ from a
fixed *scenario* (emitters, antenna pattern, multi-element array
steering). The comms simulator does not have emitters in that sense
-- the IQ stream IS the data the peer transmitter wrote. Conflating
the two would force callers to author dummy scenarios for every
comms test, and the receiver's render path would have to learn a
"pass-through" mode that contradicts its current design.

CHANNEL EFFECTS

Two impairments are wired in Iter 3:

* **AWGN** at a configured ``chip_snr_db`` (chip-level Eb_c/N0).
  Same formula as ``rfmesh_dsss/tests/test_ber_honesty.py``'s
  ``_awgn_channel_chip_level``: ``sigma_per_component =
  sqrt(1 / (2 * eb_c_n0_linear))``. Applied per ``write`` call
  before the samples enter the buffer.
* **Multipath FIR** with explicit complex taps. Applied per
  ``write`` call via ``np.convolve(mode='same')``. The single-path
  default (``taps=(1.0+0j,)``) is identity.

Stochastic / impairing channels added later (log-normal shadowing,
chip-level CFO, IQ imbalance, ADC quantisation) slot into the same
``write``-time application chain; the test suite documents which
ones land per iteration.

LIFECYCLE NOTES

* ``LoopbackChannel`` is shared mutable state by design -- the
  transmitter and the receiver each hold a reference and the
  framework's tests construct one channel, hand it to both, and
  trust ordering.
* ``write`` is non-blocking and append-only. ``read`` raises if the
  buffer is short rather than blocking or padding with zeros (B3 --
  the comms loop's contract is "exactly n samples or raise", same as
  every other Receiver).
* No thread-safety in v1.3.0. Iter 4+ may add an asyncio-friendly
  ``LoopbackChannel`` variant when the node-runtime comms loop
  needs concurrent producer / consumer access.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from typing import Final

import numpy as np
from rfmesh_contracts import NodeConfig  # type: ignore[import-untyped, unused-ignore]

from ..exceptions import InvalidReadSizeError, ReceiverNotOpenError
from .capabilities import SyntheticReceiverCapabilities

#: Default identity multipath: a single unit tap at zero delay.
_IDENTITY_MULTIPATH_TAPS: Final[tuple[complex, ...]] = (complex(1.0, 0.0),)


class LoopbackChannel:
    """In-process IQ pipe with optional AWGN + multipath FIR.

    Parameters
    ----------
    sample_rate_hz:
        Authoritative sample rate of the loopback. Receivers and
        transmitters that share this channel both report this rate via
        their ``capabilities()``.
    chip_snr_db:
        Channel SNR per BPSK chip (Eb_c/N0 in dB). ``None`` disables
        AWGN -- the channel becomes lossless. The DSSS BER honesty
        derivation assumes one sample per chip, so for the v1.3.0
        comms simulator the chip rate equals the sample rate.
    multipath_taps:
        Complex FIR taps describing a fixed multipath profile applied
        on every ``write``. The default ``(1+0j,)`` is identity. Two
        canonical test profiles: a strong direct + delayed echo
        ``(1+0j, 0.5+0j)`` and a 3-tap profile that models a
        forest-edge reflector.
    rng_seed:
        Seed for the per-channel ``np.random.Generator`` used by
        AWGN. Two channels constructed with the same seed produce
        deterministic, byte-identical AWGN sequences -- the property
        the DSSS golden / Monte-Carlo tests depend on.
    """

    def __init__(
        self,
        sample_rate_hz: float,
        chip_snr_db: float | None = None,
        multipath_taps: Sequence[complex] = _IDENTITY_MULTIPATH_TAPS,
        rng_seed: int = 0,
    ) -> None:
        if sample_rate_hz <= 0:
            msg = f"sample_rate_hz must be > 0 (got {sample_rate_hz})."
            raise ValueError(msg)
        if not multipath_taps:
            msg = "multipath_taps must be non-empty (use (1+0j,) for identity)."
            raise ValueError(msg)
        self._sample_rate_hz = float(sample_rate_hz)
        self._chip_snr_db = None if chip_snr_db is None else float(chip_snr_db)
        self._taps = np.asarray(multipath_taps, dtype=np.complex64)
        self._rng = np.random.default_rng(rng_seed)
        self._buffer: deque[np.complex64] = deque()

    @property
    def sample_rate_hz(self) -> float:
        """Authoritative sample rate, Hz. Read-only after construction."""
        return self._sample_rate_hz

    @property
    def chip_snr_db(self) -> float | None:
        """Configured chip-level SNR, dB. ``None`` means the channel is lossless."""
        return self._chip_snr_db

    def available(self) -> int:
        """Number of complex samples currently buffered (ready for ``read``)."""
        return len(self._buffer)

    def reset(self) -> None:
        """Discard buffered samples. Used between independent test scenarios."""
        self._buffer.clear()

    def write(self, iq: np.ndarray) -> int:
        """Apply channel impairments to ``iq`` and append to the buffer.

        Returns the number of samples written (equal to ``len(iq)``;
        the channel is non-blocking and cannot drop). Validates input
        shape / dtype rather than silently coercing -- the
        ``Transmitter.write`` contract is "exactly len(iq) or raise".
        """
        if iq.ndim != 1:
            msg = f"LoopbackChannel.write: iq must be 1-D (got shape {iq.shape})."
            raise ValueError(msg)
        if iq.size == 0:
            msg = "LoopbackChannel.write: iq must be non-empty."
            raise ValueError(msg)
        # Use complex128 internally for the impairment chain (avoid
        # accumulated float32 precision loss across long captures);
        # cast back to complex64 at the buffer-append boundary.
        x = np.asarray(iq, dtype=np.complex128)
        x = self._apply_multipath(x)
        x = self._apply_awgn(x)
        out = x.astype(np.complex64)
        self._buffer.extend(out.tolist())
        return int(iq.size)

    def read(self, n_samples: int) -> np.ndarray:
        """Pop exactly ``n_samples`` from the buffer; raise if fewer are ready.

        Mirror of ``Receiver.read``'s hard guarantee (B3). The
        ``LoopbackReceiver`` calls into this; calling directly is
        legal for tests that want to inspect the channel separately
        from a Receiver lifecycle.
        """
        if n_samples <= 0:
            msg = f"LoopbackChannel.read: n_samples must be > 0 (got {n_samples})."
            raise ValueError(msg)
        if len(self._buffer) < n_samples:
            msg = (
                f"LoopbackChannel.read: requested {n_samples} samples, "
                f"only {len(self._buffer)} available. The transmitter must "
                "write before the receiver reads -- short reads are not "
                "padded silently (B3)."
            )
            raise BufferError(msg)
        out = np.empty(n_samples, dtype=np.complex64)
        for i in range(n_samples):
            out[i] = self._buffer.popleft()
        return out

    def _apply_multipath(self, x: np.ndarray) -> np.ndarray:
        """Convolve ``x`` with the configured FIR taps (identity by default)."""
        if self._taps.size == 1 and self._taps[0] == complex(1.0, 0.0):
            return x
        return np.convolve(x, self._taps.astype(np.complex128), mode="same")

    def _apply_awgn(self, x: np.ndarray) -> np.ndarray:
        """Add complex AWGN at the configured chip-level Eb_c/N0."""
        if self._chip_snr_db is None:
            return x
        eb_c_n0_linear = 10.0 ** (self._chip_snr_db / 10.0)
        sigma_component = math.sqrt(1.0 / (2.0 * eb_c_n0_linear))
        noise = (
            self._rng.standard_normal(x.size)
            + 1j * self._rng.standard_normal(x.size)
        ) * sigma_component
        return x + noise


class LoopbackReceiver:
    """Receiver Protocol that reads from a ``LoopbackChannel``.

    Structurally satisfies ``rfmesh_contracts.protocols.Receiver``.
    Used by the comms simulator's RX-side node to consume IQ that the
    paired ``SyntheticTransmitter`` wrote into the shared channel.

    Lifecycle: ``open()`` -> ``configure(NodeConfig)`` -> ``read(n)``
    repeatedly -> ``close()``. Same shape as ``SyntheticReceiver`` so
    the node-runtime composition layer can swap one for the other.
    """

    def __init__(self, channel: LoopbackChannel) -> None:
        self._channel = channel
        self._is_open = False
        self._node_config: NodeConfig | None = None

    @property
    def channel(self) -> LoopbackChannel:
        """The shared loopback channel this receiver reads from."""
        return self._channel

    def open(self) -> None:
        """Mark the receiver opened. Idempotent."""
        self._is_open = True

    def configure(self, config: NodeConfig) -> None:
        """Record the node config.

        The loopback's sample rate is authoritative; the call does
        not retune anything but stores the config so a future
        runtime can introspect intended tuning.
        """
        self._node_config = config

    def read(self, n_samples: int) -> np.ndarray:
        """Return exactly ``n_samples`` complex64 from the loopback.

        Raises:
            ReceiverNotOpenError: if ``open()`` has not been called,
                or ``close()`` has run since the last ``open()``.
            InvalidReadSizeError: if ``n_samples <= 0``.
            BufferError: if the channel has fewer than ``n_samples``
                buffered. The Receiver contract forbids silent short
                reads; the test fixture must write enough before the
                read fires.
        """
        if not self._is_open:
            msg = "LoopbackReceiver.read() called before open() (or after close())."
            raise ReceiverNotOpenError(msg)
        if n_samples <= 0:
            msg = f"LoopbackReceiver.read(n) requires n > 0 (got {n_samples})."
            raise InvalidReadSizeError(msg)
        return self._channel.read(n_samples)

    def capabilities(self) -> SyntheticReceiverCapabilities:
        """Return the capability snapshot (driver = ``loopback``, 1 channel)."""
        return SyntheticReceiverCapabilities(
            driver="loopback",
            n_coherent_channels=1,
            actual_sample_rate_hz=self._channel.sample_rate_hz,
            is_power_calibrated=False,
        )

    def close(self) -> None:
        """Mark the receiver closed. Idempotent."""
        self._is_open = False
