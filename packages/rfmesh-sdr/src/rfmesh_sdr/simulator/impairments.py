"""Per-channel and analog/ADC-stage receiver impairments.

Two unrelated abstractions live in this module:

* ``ChannelImpairments`` (WS-A-002) -- per-channel complex offsets between
  the antenna and the ADC, applied during *coherent* render so a
  ``calibrate()`` handshake can recover them. By convention channel 0 is
  the reference (``channel_offsets[0] == 1 + 0j``).
* ``ReceiverImpairments`` (WS-A-003) -- analog and ADC-stage effects
  applied *after* AWGN and steering, common across channels: IQ imbalance,
  DC offset, and ADC quantization. Implementations are stackable via
  ``CompositeReceiverImpairments``; the canonical analog-chain order is
  DC offset, then IQ imbalance, then ADC quantization.

The two abstractions live together because they share the "stuff between
the noise source and the ADC" framing -- the difference is that
``ChannelImpairments`` is per-RF-path (per-channel) and recoverable by
calibration, whereas ``ReceiverImpairments`` models effects that the
calibration handshake does not address (an IQ imbalance and a DC offset
on the I/Q baseband are common to all channels and do not show up in the
cross-channel correlations the WS-A-002 handshake measures).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class ChannelImpairments:
    """Frozen description of per-channel complex offsets between RF path and ADC.

    Attributes:
        channel_offsets: A ``(n_elements,)`` ``complex128`` array. Element i
            is the per-channel complex offset (``|offset| * exp(j * phase_offset)``);
            element 0 must be exactly ``1 + 0j`` by reference convention.
    """

    channel_offsets: np.ndarray

    def __post_init__(self) -> None:
        """Validate shape, dtype, and the channel-0 reference convention."""
        if self.channel_offsets.dtype != np.complex128:
            msg = (
                "ChannelImpairments.channel_offsets must be dtype complex128 "
                f"(got {self.channel_offsets.dtype})."
            )
            raise ValueError(msg)
        if self.channel_offsets.ndim != 1:
            msg = (
                "ChannelImpairments.channel_offsets must be 1-D "
                f"(got shape {self.channel_offsets.shape})."
            )
            raise ValueError(msg)
        if self.channel_offsets.size < 1:
            msg = "ChannelImpairments.channel_offsets must have at least one element."
            raise ValueError(msg)
        ch0 = self.channel_offsets[0]
        if ch0 != 1.0 + 0.0j:
            msg = (
                "ChannelImpairments.channel_offsets[0] must be exactly 1+0j "
                f"(reference channel by convention); got {ch0}."
            )
            raise ValueError(msg)
        self.channel_offsets.flags.writeable = False

    @property
    def n_elements(self) -> int:
        """How many channels this impairment vector covers."""
        return int(self.channel_offsets.shape[0])


# ---------------------------------------------------------------------------
# Receiver-side analog/ADC impairments (WS-A-003)
# ---------------------------------------------------------------------------


@runtime_checkable
class ReceiverImpairments(Protocol):
    """Analog/ADC-stage impairments applied to baseband IQ after AWGN.

    The impairment sits between the antenna+AWGN combiner and the
    quantizer in the receiver model -- IQ imbalance is an analog
    direct-conversion artefact, DC offset comes from LO leakage and
    op-amp bias, and ADC quantization is the final fixed-point step.

    Implementations broadcast across 1-D ``(n,)`` single-channel buffers
    and 2-D ``(n_channels, n)`` coherent buffers identically (each
    affects each sample / each channel independently). ``rng`` is part
    of the Protocol so future stochastic impairments (e.g. phase noise)
    drop in without a signature break; the deterministic v1.0 impairments
    accept and ignore it.
    """

    def apply(
        self,
        samples: np.ndarray,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Return ``samples`` after this impairment's effect.

        Output shape and dtype match the input; the simulator's final
        ``astype(complex64)`` cast happens after this stage.
        """
        ...


@dataclass(frozen=True)
class IdentityReceiverImpairments:
    """Pass-through impairment: returns ``samples`` unchanged.

    The default for ``SimulationScenario.receiver_impairments`` -- the
    presence of the wiring point is invisible to a scenario that does not
    configure receiver impairments, preserving WS-A-001/002 byte-equality.
    """

    def apply(
        self,
        samples: np.ndarray,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Return ``samples`` unchanged (no-op)."""
        del rng
        return samples


@dataclass(frozen=True)
class IQImbalance:
    """Direct-conversion IQ imbalance: gain and phase mismatch between I and Q.

    The model leaves the I branch unity and adds gain/phase error to the Q
    branch: ``y = Re(x) + j * beta * exp(j * phi) * Im(x)``, where
    ``beta = 10**(amplitude_db / 20)`` and ``phi = radians(phase_deg)``.

    For a CW input at baseband offset ``+f_0`` this produces an image tone
    at ``-f_0`` whose level matches the textbook image-rejection ratio

        IRR_dB ~= -20 * log10(0.5 * |epsilon|),  epsilon = 1 - beta*exp(j*phi)

    The image bin appears because the two mismatched branches are
    equivalent to a leakage from ``x`` into ``x*`` (the conjugate-symmetric
    image at the negative frequency).

    Attributes:
        amplitude_db: Q-branch gain mismatch in dB. 0 dB = matched.
        phase_deg: Q-branch phase error in degrees. 0 deg = matched.
    """

    amplitude_db: float
    phase_deg: float

    def apply(
        self,
        samples: np.ndarray,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Apply Q-branch gain/phase error to the imaginary component."""
        del rng  # deterministic impairment
        g = 10.0 ** (self.amplitude_db / 20.0)
        phi = math.radians(self.phase_deg)
        beta = g * (math.cos(phi) + 1j * math.sin(phi))
        out: np.ndarray = (samples.real + 1j * (beta * samples.imag)).astype(np.complex128)
        return out


@dataclass(frozen=True)
class DCOffset:
    """Additive complex DC offset (LO leakage / op-amp bias).

    Adds the complex constant ``i_volts + j*q_volts`` to every sample. In
    the PSD this appears as a tone at DC with magnitude
    ``sqrt(i^2 + q^2)``; bins away from DC are untouched.

    Attributes:
        i_volts: I-axis DC offset (same arbitrary "volts" scale as the IQ
            samples -- the SDR is not power-calibrated, so the scale is
            relative to whatever full-scale the upstream produces).
        q_volts: Q-axis DC offset.
    """

    i_volts: float
    q_volts: float

    def apply(
        self,
        samples: np.ndarray,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Add the complex DC offset to every sample, broadcasting over shape."""
        del rng  # deterministic impairment
        offset = self.i_volts + 1j * self.q_volts
        out: np.ndarray = (samples + offset).astype(np.complex128)
        return out


@dataclass(frozen=True)
class ADCQuantization:
    """Uniform mid-tread quantization of I and Q to a signed integer grid.

    The model clips each axis to ``[-1, +1]`` (saturating, not wrapping --
    real ADCs do not wrap), then rounds to one of ``2**(bits - 1) - 1``
    signed integer levels per axis. For ``bits = 8`` that is 127 positive
    levels (and 127 negative + 0 = 255 total values per axis -- the
    canonical signed 8-bit grid).

    The resulting SQNR for a near-full-scale complex tone is the textbook
    ``6.02 * bits + 1.76 dB`` (49.9 dB for 8-bit), independent of any
    upstream noise floor when quantization dominates.

    Attributes:
        bits: ADC resolution in bits, >= 1.
    """

    bits: int

    def __post_init__(self) -> None:
        """Require a meaningful resolution."""
        if self.bits < 1:
            msg = f"ADCQuantization.bits must be >= 1 (got {self.bits})."
            raise ValueError(msg)

    def apply(
        self,
        samples: np.ndarray,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Clip to ``[-1, +1]`` then quantise I and Q to the integer grid."""
        del rng  # deterministic impairment
        levels = (1 << (self.bits - 1)) - 1
        scale = float(levels)
        re_clipped = np.clip(samples.real, -1.0, 1.0)
        im_clipped = np.clip(samples.imag, -1.0, 1.0)
        re_q = np.round(re_clipped * scale) / scale
        im_q = np.round(im_clipped * scale) / scale
        out: np.ndarray = (re_q + 1j * im_q).astype(np.complex128)
        return out


@dataclass(frozen=True)
class CompositeReceiverImpairments:
    """Chain a tuple of ``ReceiverImpairments`` in declared order.

    The canonical analog-chain order is
    ``(DCOffset, IQImbalance, ADCQuantization)`` -- DC adds at the front
    of the analog stage, IQ imbalance follows from the I/Q split, and
    quantization is the ADC at the back. The caller is free to pick a
    different order; ``CompositeReceiverImpairments`` simply chains.

    Attributes:
        impairments: Ordered tuple of ``ReceiverImpairments`` to apply.
    """

    impairments: tuple[ReceiverImpairments, ...]

    def __post_init__(self) -> None:
        """Require at least one inner impairment."""
        if len(self.impairments) == 0:
            msg = "CompositeReceiverImpairments requires at least one inner impairment."
            raise ValueError(msg)

    def apply(
        self,
        samples: np.ndarray,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Apply each inner impairment in declared order to the running buffer."""
        out = samples
        for impair in self.impairments:
            out = impair.apply(out, rng)
        return out
