"""Per-channel impairments injected during coherent-mode rendering.

A real phase-coherent SDR has cable phase mismatches, gain-stage variation,
and other per-channel quirks between the antenna and the ADC -- the "stuff
between the noise source and the ADC" that ``calibrate()`` is meant to
recover. ``ChannelImpairments`` is the scenario-level switch for modelling
those offsets: the receiver multiplies channel i's per-emitter contribution
by ``channel_offsets[i]`` during render, simulating exactly that physical
RF path.

By convention channel 0 is the reference: ``channel_offsets[0]`` must be
exactly ``1 + 0j``. Calibration of the simulator's coherent stream
measures offsets relative to channel 0 and computes their inverse; with
the reference fixed at unity the recovered correction is the inverse of
the impairment as injected, modulo finite-sample measurement noise.
"""

from __future__ import annotations

from dataclasses import dataclass

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
