"""Carrier-phase correction for BPSK despread symbols.

The matched filter in ``correlation`` locates the frame start; the
spreader/despreader handles chip-level alignment; what remains is the
**constant phase rotation** introduced between the transmitter and
receiver local oscillators. For BPSK that rotation degrades the
sign-of-real-part decision -- a 90 degree rotation puts all the
energy on the imaginary axis and the demodulator's BER goes to 0.5.

ALGORITHM -- BLOCK-MODE COSTAS-LIKE ESTIMATOR

* For BPSK the transmitted symbols are ``+/-1`` (real). Squaring
  removes the modulation: ``(+1)**2 = (-1)**2 = +1``. The mean of
  ``symbols**2`` then has phase ``2 * theta`` where ``theta`` is the
  unknown carrier-phase offset (in the noise-free limit).
* Recover ``theta = angle(mean(symbols**2)) / 2``.
* De-rotate: ``symbols * exp(-1j * theta)``. After this the symbols
  lie close to the real axis and the BPSK demodulator's hard
  decision is robust again.

This is a **block-mode** estimator: it consumes one block of despread
symbols and applies one phase correction. The realtime Costas-loop
version (per-symbol PLL with a loop filter) lands in Iter 3 with the
SDR streaming integration. For Iter 2's offline simulator the block
estimate is sufficient -- the simulator's channel injects a single
constant phase, not a slowly varying one.

CHIP-RATE / SYMBOL-RATE TIMING RECOVERY -- DEFERRED TO ITER 3

Sub-chip timing recovery (early-late gate, etc.) needs oversampled
input -- the receiver must have more than one IQ sample per chip to
slide a timing estimate around. The Iter 2 simulator runs at exactly
one sample per chip; there is no sub-chip alignment to recover, so
this module ships *without* the early-late primitive. Iter 3 adds it
alongside the SDR oversampling layer; the realtime path inserts a
chip-rate timing block between ``correlation`` and ``despread``.

DSP CORRECTNESS NOTES

* The estimator has a 180-degree ambiguity by construction: ``theta``
  and ``theta + pi`` produce identical ``symbols**2`` outputs. After
  de-rotation BPSK symbols may end up flipped (``+1`` -> ``-1``). The
  framing-layer CRC catches this: a wholesale bit-flip across the
  whole frame changes the CRC, the decoder raises, and the comms loop
  retries the next slot. This is acceptable for v1.3.0; a
  differential-encoding overlay that removes the ambiguity is
  parking-lot for v1.4+.
* On a zero-mean input (e.g. all-noise) the squared-mean has
  near-zero magnitude; the recovered phase is meaningless and the
  estimator is documented to return ``0.0`` (no de-rotation) in that
  case rather than amplify noise.
"""

from __future__ import annotations

import numpy as np

from .constants import EPSILON
from .exceptions import DsssError


def estimate_carrier_phase(symbols: np.ndarray) -> float:
    """Return the estimated carrier-phase offset (radians) of a BPSK block.

    Computes ``angle(mean(symbols**2)) / 2``. The block average
    removes the modulation; the result is the constant phase rotation
    introduced by the channel between TX and RX.

    Parameters
    ----------
    symbols:
        1-D complex symbol array; typically the output of
        ``despread`` for one frame's worth of symbols.

    Returns
    -------
    Estimated phase in radians, in ``(-pi/2, +pi/2]``. Returns
    exactly ``0.0`` when ``|mean(symbols**2)| < EPSILON`` (no
    signal in the block -- de-rotating would amplify noise).
    """
    if symbols.ndim != 1:
        msg = f"symbols must be 1-D (got shape {symbols.shape})."
        raise DsssError(msg)
    if symbols.size == 0:
        msg = "symbols must be non-empty."
        raise DsssError(msg)
    sym_c = np.asarray(symbols, dtype=np.complex64)
    squared_mean = np.mean(sym_c.astype(np.complex128) ** 2)
    if abs(squared_mean) < EPSILON:
        return 0.0
    return float(np.angle(squared_mean) / 2.0)


def correct_carrier_phase(symbols: np.ndarray) -> np.ndarray:
    """De-rotate ``symbols`` by the block-estimated phase offset.

    Convenience wrapper: ``estimate_carrier_phase`` + multiply by
    ``exp(-1j * theta)``. Returns a new complex64 array; does not
    mutate the input.

    Parameters
    ----------
    symbols:
        1-D complex symbol block (output of ``despread``).

    Returns
    -------
    1-D complex64 array, same length as ``symbols``, with the
    block-constant carrier phase rotation removed. BPSK symbols
    after this call lie close to the real axis (subject to noise
    and the 180-degree ambiguity documented in the module docstring).
    """
    theta = estimate_carrier_phase(symbols)
    if theta == 0.0:
        out_passthrough: np.ndarray = np.asarray(symbols, dtype=np.complex64).copy()
        return out_passthrough
    sym_c = np.asarray(symbols, dtype=np.complex64)
    derotator = np.exp(np.complex64(-1j * theta))
    out: np.ndarray = (sym_c * derotator).astype(np.complex64)
    return out
