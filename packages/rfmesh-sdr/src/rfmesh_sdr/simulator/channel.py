"""Channel models for the simulator -- the SDR-side abstraction of RF propagation.

``ChannelModel`` is a Protocol so the simulator can grow multipath /
two-ray-ground / log-normal-shadowing implementations without
``SyntheticReceiver`` having to learn about any of them. WS-A-001 shipped
``FreeSpaceChannel``; WS-A-003 adds:

* ``TwoRayGroundChannel`` -- direct path plus a single ground-reflected
  ray (``Gamma = -1`` for grazing incidence). Produces the canonical
  interference pattern: oscillation around free-space within ``d_bp``
  followed by a smooth ``1/d^4`` decline beyond it (with the deepest null
  at ``d = 2 h_tx h_rx / lambda = d_bp / 2``).
* ``MultipathFIRChannel`` -- post-propagation FIR with explicit
  ``(tap, delay)`` pairs. The impulse response is built once in
  ``__post_init__``; ``apply`` does a single ``np.convolve(mode='same')``.
* ``LogNormalShadowing`` -- one log-normal fade per ``apply`` call.
  Stochastic; consumes one sample from ``rng``. Used to layer scenario-
  level coarse shadowing on top of the deterministic channels.
* ``CompositeChannel`` -- chains a tuple of ``ChannelModel``\\ s in order.
  The canonical scenario stack is
  ``(FreeSpaceChannel, MultipathFIRChannel, LogNormalShadowing)``; the
  deterministic / stochastic ordering matters only for ``rng`` budgeting,
  not for the linear-system result.

WHY A PROTOCOL HERE AND NOT AN ABC
----------------------------------
Same reasoning as the contracts package: structural typing keeps the
dependency graph clean. ``SyntheticReceiver`` consumes a ``ChannelModel``;
each implementation is one by shape, not by inheritance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np

_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_FRIIS_FOUR_PI = 4.0 * math.pi
_DB_AMPLITUDE_DIVISOR = 20.0


@runtime_checkable
class ChannelModel(Protocol):
    """Apply RF channel effects to one emitter's baseband sample block.

    ``rng`` is part of the protocol because stochastic channels
    (``LogNormalShadowing``, future fading models) need a Generator.
    Deterministic channels (``FreeSpaceChannel``, ``TwoRayGroundChannel``,
    ``MultipathFIRChannel``) accept ``rng`` and ignore it so the call site
    in ``SyntheticReceiver`` is uniform.
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
        del rng  # deterministic channel
        if distance_m <= 0.0:
            msg = f"FreeSpaceChannel.apply: distance_m must be > 0 (got {distance_m})."
            raise ValueError(msg)
        if frequency_hz <= 0.0:
            msg = f"FreeSpaceChannel.apply: frequency_hz must be > 0 (got {frequency_hz})."
            raise ValueError(msg)
        wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
        amplitude_factor = wavelength_m / (_FRIIS_FOUR_PI * distance_m)
        return samples * amplitude_factor


@dataclass(frozen=True)
class TwoRayGroundChannel:
    """Two-ray ground-reflection model: direct path plus a single specular bounce.

    The combined field at the receiver is

        E_total = (lambda/(4*pi)) * [ exp(-j*k*d_los)/d_los
                                    - exp(-j*k*d_grd)/d_grd ]

    where ``d_los = sqrt(d^2 + (h_tx - h_rx)^2)`` is the direct path,
    ``d_grd = sqrt(d^2 + (h_tx + h_rx)^2)`` is the ground-reflected path
    via the image source, and the leading minus sign on the reflected
    term is the grazing-incidence reflection coefficient ``Gamma = -1``
    (a fine approximation for both polarizations at low elevation angles).

    The breakpoint distance ``d_bp = 4*h_tx*h_rx/lambda`` marks the
    transition from the oscillating near-field regime (where the response
    swings between ``-inf`` dB nulls at ``d = 2 h h / (n*lambda)`` and
    ``+6 dB`` peaks at ``d = 4 h h / ((2n+1)*lambda)``) to the smooth
    ``1/d^4`` far-field regime beyond ``d_bp``.

    Attributes:
        height_tx_m: Transmitter antenna height above the ground, metres.
        height_rx_m: Receiver antenna height above the ground, metres.
    """

    height_tx_m: float
    height_rx_m: float

    def __post_init__(self) -> None:
        """Validate antenna heights at construction (Invariant 4 surface)."""
        if self.height_tx_m <= 0.0:
            msg = f"TwoRayGroundChannel.height_tx_m must be > 0 (got {self.height_tx_m})."
            raise ValueError(msg)
        if self.height_rx_m <= 0.0:
            msg = f"TwoRayGroundChannel.height_rx_m must be > 0 (got {self.height_rx_m})."
            raise ValueError(msg)

    def apply(
        self,
        samples: np.ndarray,
        distance_m: float,
        frequency_hz: float,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Multiply ``samples`` by the two-ray complex amplitude factor for (d, f)."""
        del rng  # deterministic channel
        if distance_m <= 0.0:
            msg = f"TwoRayGroundChannel.apply: distance_m must be > 0 (got {distance_m})."
            raise ValueError(msg)
        if frequency_hz <= 0.0:
            msg = f"TwoRayGroundChannel.apply: frequency_hz must be > 0 (got {frequency_hz})."
            raise ValueError(msg)
        wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
        k = 2.0 * math.pi / wavelength_m
        h_diff = self.height_tx_m - self.height_rx_m
        h_sum = self.height_tx_m + self.height_rx_m
        d_los = math.sqrt(distance_m * distance_m + h_diff * h_diff)
        d_grd = math.sqrt(distance_m * distance_m + h_sum * h_sum)
        scale = wavelength_m / _FRIIS_FOUR_PI
        # Gamma = -1: the reflected ray subtracts from the direct ray after
        # propagation-delay phases are applied.
        e_los = scale * (1.0 / d_los) * np.exp(-1j * k * d_los)
        e_grd = scale * (1.0 / d_grd) * np.exp(-1j * k * d_grd)
        amplitude_factor = e_los - e_grd
        out: np.ndarray = (samples * amplitude_factor).astype(np.complex128)
        return out


@dataclass(frozen=True)
class MultipathFIRChannel:
    """Post-propagation multipath as an explicit-tap FIR filter.

    The channel response is a sum of complex weighted, integer-delayed
    copies of the input: ``y[n] = sum_k tap_k * x[n - delay_k]``. The
    impulse response array is constructed once in ``__post_init__`` (an
    array of length ``max(delays) + 1`` with non-zero entries at the tap
    positions), so each ``apply`` does one ``np.convolve``.

    The channel is *post-propagation* in the canonical scenario stack:
    ``CompositeChannel((FreeSpaceChannel(), MultipathFIRChannel(...)))``
    applies the Friis scalar first, then the multipath filter. The class
    itself does not embed the Friis scalar -- compose it explicitly.

    Attributes:
        taps: ``(n_taps,)`` ``complex128`` tap weights.
        delays: ``(n_taps,)`` ``int64`` integer sample delays (>= 0).
    """

    taps: np.ndarray
    delays: np.ndarray
    _impulse_response: np.ndarray = field(
        init=False,
        repr=False,
        compare=False,
        default_factory=lambda: np.zeros(0, dtype=np.complex128),
    )

    def __post_init__(self) -> None:
        """Validate taps / delays and pre-compute the impulse response."""
        if self.taps.dtype != np.complex128:
            msg = f"MultipathFIRChannel.taps must be dtype complex128 (got {self.taps.dtype})."
            raise ValueError(msg)
        if self.taps.ndim != 1:
            msg = f"MultipathFIRChannel.taps must be 1-D (got shape {self.taps.shape})."
            raise ValueError(msg)
        if self.delays.ndim != 1:
            msg = f"MultipathFIRChannel.delays must be 1-D (got shape {self.delays.shape})."
            raise ValueError(msg)
        if self.taps.shape != self.delays.shape:
            msg = (
                "MultipathFIRChannel.taps and .delays must have the same shape "
                f"(got {self.taps.shape} vs {self.delays.shape})."
            )
            raise ValueError(msg)
        if self.taps.size == 0:
            msg = "MultipathFIRChannel requires at least one tap."
            raise ValueError(msg)
        if np.any(self.delays < 0):
            msg = f"MultipathFIRChannel.delays must all be >= 0 (got {self.delays.tolist()})."
            raise ValueError(msg)
        max_delay = int(self.delays.max())
        ir = np.zeros(max_delay + 1, dtype=np.complex128)
        # Sum repeated delays so a (tap, delay) list with duplicates resolves
        # to the same impulse response as merging them manually.
        for tap, delay in zip(self.taps, self.delays, strict=True):
            ir[int(delay)] += tap
        ir.flags.writeable = False
        object.__setattr__(self, "_impulse_response", ir)

    @property
    def impulse_response(self) -> np.ndarray:
        """The cached ``(max_delay + 1,)`` ``complex128`` impulse response."""
        return self._impulse_response

    def apply(
        self,
        samples: np.ndarray,
        distance_m: float,
        frequency_hz: float,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Convolve ``samples`` with the FIR impulse response (mode='same')."""
        del distance_m, frequency_hz, rng  # multipath FIR is range/freq/RNG-independent
        out: np.ndarray = np.convolve(samples, self._impulse_response, mode="same").astype(
            np.complex128
        )
        return out


@dataclass(frozen=True)
class LogNormalShadowing:
    """Scenario-static log-normal shadowing: one fade draw per ``apply`` call.

    A log-normal channel models slow, large-scale variation in the
    received amplitude. The fade is drawn once per ``apply`` (one
    realisation per IQ block) rather than per sample -- this matches the
    scenario-static assumption the WS-A-003 ticket pins explicitly
    (no Doppler, no time-varying fading).

    The fade in dB is ``N(0, sigma_db)``; the amplitude scale factor is
    ``10**(fade_db / 20)``. After ``apply``, ``20 * log10(|amp|)`` measured
    over many independent scenarios has standard deviation ``sigma_db``.

    Attributes:
        sigma_db: Standard deviation of the per-realisation fade in dB.
    """

    sigma_db: float

    def __post_init__(self) -> None:
        """Validate sigma is non-negative."""
        if self.sigma_db < 0.0:
            msg = f"LogNormalShadowing.sigma_db must be >= 0 (got {self.sigma_db})."
            raise ValueError(msg)

    def apply(
        self,
        samples: np.ndarray,
        distance_m: float,
        frequency_hz: float,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Multiply ``samples`` by one log-normal fade for the whole block."""
        del distance_m, frequency_hz  # shadowing is range/freq-independent at v1
        fade_db = float(rng.standard_normal()) * self.sigma_db
        fade_amp = 10.0 ** (fade_db / _DB_AMPLITUDE_DIVISOR)
        out: np.ndarray = samples * fade_amp
        return out


@dataclass(frozen=True)
class CompositeChannel:
    """Chain a tuple of channels in order: ``c_k.apply(... c_1.apply(c_0.apply(x)))``.

    The canonical scenario stack is
    ``(FreeSpaceChannel, MultipathFIRChannel, LogNormalShadowing)``. The
    individual channels are linear-time-invariant (free-space and multipath)
    or scalar (shadowing) and so commute under multiplication; the order
    only affects ``rng`` budgeting (a stochastic channel later in the chain
    sees the same ``rng`` state as one earlier, because the deterministic
    intermediaries do not draw).

    Attributes:
        channels: Ordered tuple of ``ChannelModel`` to chain.
    """

    channels: tuple[ChannelModel, ...]

    def __post_init__(self) -> None:
        """Require at least one inner channel."""
        if len(self.channels) == 0:
            msg = "CompositeChannel requires at least one inner channel."
            raise ValueError(msg)

    def apply(
        self,
        samples: np.ndarray,
        distance_m: float,
        frequency_hz: float,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Apply each inner channel in declared order to the running buffer."""
        out = samples
        for channel in self.channels:
            out = channel.apply(out, distance_m, frequency_hz, rng)
        return out
