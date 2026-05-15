"""``Calibration`` -- the recovered inverse offsets from a noise-source handshake.

When a coherent ``SyntheticReceiver`` runs ``calibrate()``, the simulator
models a built-in noise source broadcast equally to every RX channel
through the array's internal RF distribution. The signal at channel i is
``noise * impairment_i + adc_noise_i``; cross-correlating each channel
against channel 0 recovers an estimate of ``impairment_i`` (the ADC noise
decorrelates across channels and falls out of the cross term). The
calibration record stores the *inverse* of those measured impairments,
so applying ``calibration.complex_offsets[i]`` per channel during render
cancels the impairment back to identity.

Per the design hint in WS-A-002: calibration is in-memory only for this
ticket; ADR-004 will introduce a calibration-file format and on-disk
persistence. The ``Calibration`` record stays a value object the
receiver caches on itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_TWO_D = 2


@dataclass(frozen=True)
class Calibration:
    """Per-channel complex correction factors recovered by the noise-source handshake.

    Attributes:
        complex_offsets: A ``(n_elements,)`` ``complex128`` array. Element i
            multiplies channel i's per-emitter contribution during render.
            Element 0 is exactly ``1 + 0j`` (channel 0 is the reference and is
            never corrected against itself).
    """

    complex_offsets: np.ndarray

    def __post_init__(self) -> None:
        """Validate shape, dtype, and the channel-0 invariant."""
        if self.complex_offsets.dtype != np.complex128:
            msg = (
                "Calibration.complex_offsets must be dtype complex128 "
                f"(got {self.complex_offsets.dtype})."
            )
            raise ValueError(msg)
        if self.complex_offsets.ndim != 1:
            msg = (
                f"Calibration.complex_offsets must be 1-D (got shape {self.complex_offsets.shape})."
            )
            raise ValueError(msg)
        if self.complex_offsets.size < 1:
            msg = "Calibration.complex_offsets must have at least one element."
            raise ValueError(msg)
        ch0 = self.complex_offsets[0]
        if ch0 != 1.0 + 0.0j:
            msg = (
                "Calibration.complex_offsets[0] must be exactly 1+0j "
                f"(channel 0 is the reference); got {ch0}."
            )
            raise ValueError(msg)
        self.complex_offsets.flags.writeable = False

    @property
    def n_elements(self) -> int:
        """How many channels this calibration covers."""
        return int(self.complex_offsets.shape[0])

    def apply_to_block(self, block: np.ndarray) -> np.ndarray:
        """Multiply each row of ``block`` by the corresponding correction factor.

        ``block`` is a coherent IQ block of shape ``(n_elements, n_samples)``.
        Returns a fresh array of the same shape and dtype as ``block`` with
        each channel scaled by ``complex_offsets[i]``. Used by tests; the
        receiver applies the offsets inline during render rather than as a
        post-process.
        """
        if block.ndim != _TWO_D:
            msg = f"Calibration.apply_to_block: block must be 2-D (got shape {block.shape})."
            raise ValueError(msg)
        if block.shape[0] != self.n_elements:
            msg = (
                "Calibration.apply_to_block: block has "
                f"{block.shape[0]} rows but calibration has {self.n_elements} channels."
            )
            raise ValueError(msg)
        scaled: np.ndarray = block * self.complex_offsets[:, np.newaxis]
        return scaled.astype(block.dtype)
