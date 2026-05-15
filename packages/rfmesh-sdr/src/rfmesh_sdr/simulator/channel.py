"""Channel models for the simulator -- the SDR-side abstraction of RF propagation.

``ChannelModel`` is a Protocol so the simulator can grow multipath /
two-ray-ground / log-normal-shadowing implementations in later tickets
(WS-A-003) without ``SyntheticReceiver`` having to learn about any of
them. v0.1 ships one implementation: ``FreeSpaceChannel``.

WHY A PROTOCOL HERE AND NOT AN ABC
----------------------------------
Same reasoning as the contracts package: structural typing keeps the
dependency graph clean. ``SyntheticReceiver`` consumes a ``ChannelModel``;
``FreeSpaceChannel`` is one by shape, not by inheritance. A future
``MultipathFIRChannel`` lands by also matching the shape, no other code
changes.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

import numpy as np

_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_FRIIS_FOUR_PI = 4.0 * math.pi


@runtime_checkable
class ChannelModel(Protocol):
    """Apply RF channel effects to one emitter's baseband sample block.

    ``rng`` is part of the protocol because future channels (multipath
    shadowing, log-normal fading) need a Generator for their stochastic
    components. ``FreeSpaceChannel`` does not use it, but every
    implementation must accept it so the call site in
    ``SyntheticReceiver`` is stable.
    """

    def apply(
        self,
        samples: np.ndarray,
        distance_m: float,
        frequency_hz: float,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Return ``samples`` after this channel's effects.

        ``samples`` is the emitter's complex baseband signal *before* path
        effects (already weighted by the antenna pattern and the emitter's
        transmit amplitude). The return is the same shape and dtype.
        """
        ...


class FreeSpaceChannel:
    """Friis-derived inverse-square amplitude attenuation. No multipath.

    For a CW tone at distance d and free-space wavelength lambda, the
    received electric-field amplitude scales by lambda / (4*pi*d) relative
    to the transmit reference. Power therefore scales by (lambda/(4*pi*d))**2,
    the canonical Friis path loss for power. The implementation multiplies
    the complex baseband samples by the *amplitude* factor; an L1
    RSSI computation (``10*log10(mean(|iq|^2))`` downstream) then sees the
    correct *power* attenuation automatically.
    """

    def apply(
        self,
        samples: np.ndarray,
        distance_m: float,
        frequency_hz: float,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Multiply ``samples`` by the Friis amplitude factor for (d, f).

        Raises ``ValueError`` for ``distance_m <= 0`` -- a colocated emitter
        would produce +inf amplitude, which is a configuration error rather
        than a physical scenario the simulator should silently model.
        """
        if distance_m <= 0.0:
            msg = f"FreeSpaceChannel.apply: distance_m must be > 0 (got {distance_m})."
            raise ValueError(msg)
        if frequency_hz <= 0.0:
            msg = f"FreeSpaceChannel.apply: frequency_hz must be > 0 (got {frequency_hz})."
            raise ValueError(msg)
        wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
        amplitude_factor = wavelength_m / (_FRIIS_FOUR_PI * distance_m)
        return samples * amplitude_factor
