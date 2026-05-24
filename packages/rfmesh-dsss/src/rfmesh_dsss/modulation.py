"""BPSK modulation / demodulation primitives.

BPSK is the v1.3.0 modulation choice for the DSSS link. Bits in
``{0, 1}`` map to symbols in ``{+1, -1}``; symbols are placed on the
real axis of the complex baseband (Q = 0). The demodulator takes
coherent IQ (typically post-despreading) and recovers bits by sign-
checking the real part.

WHY BPSK AND NOT QPSK / HIGHER-ORDER

* BPSK has the most forgiving Eb/N0 for a given BER -- ~9.6 dB Eb/N0
  for ``1e-3`` BER under AWGN. Combined with the length-1023
  spreading gain (~30 dB) the link tolerates extreme negative
  *channel* SNR while still hitting target BER.
* Symbol-rate / chip-rate ratio is 1:1 with the PN length -- no
  complication from constellation-mapping when the spreader is the
  dominant SNR mechanism.
* The pitch slide says "BPSK + length-1023 PN"; sticking to one
  modulation in v1.3.0 keeps the demo narrative tight.

CONVENTION

Bit-to-symbol map: ``0 -> +1``, ``1 -> -1``. Hard-decision
demodulation: ``Re{IQ} >= 0 -> 0``, ``Re{IQ} < 0 -> 1``. The exact
zero-handling is documented because a despread symbol exactly at the
decision boundary is a degenerate case the simulator can hit on
no-noise inputs (mapping symbols back to bits round-trips
deterministically only with a fixed boundary rule).
"""

from __future__ import annotations

import numpy as np

from .exceptions import DsssError


def bpsk_modulate(bits: np.ndarray) -> np.ndarray:
    """Map bits in ``{0, 1}`` to complex IQ symbols ``{+1+0j, -1+0j}``.

    Parameters
    ----------
    bits:
        1-D array of bits. Accepts integer dtypes; values must be
        exactly 0 or 1 (any other value raises ``DsssError`` -- silent
        coercion of e.g. 2 to 0 would hide an upstream framing bug).

    Returns
    -------
    1-D ``np.complex64`` array of the same length, with each element
    in ``{+1 + 0j, -1 + 0j}``.
    """
    if bits.ndim != 1:
        msg = f"bits must be 1-D (got shape {bits.shape})."
        raise DsssError(msg)
    bits_int = np.asarray(bits, dtype=np.int8)
    if not np.all((bits_int == 0) | (bits_int == 1)):
        msg = "bits must contain only 0 and 1; silent coercion would hide framing bugs."
        raise DsssError(msg)
    # 0 -> +1, 1 -> -1.
    symbols_real = np.where(bits_int == 0, np.float32(1.0), np.float32(-1.0))
    return symbols_real.astype(np.complex64)


def bpsk_demodulate(iq: np.ndarray) -> np.ndarray:
    """Hard-decision BPSK demodulation: recover bits from complex IQ.

    Parameters
    ----------
    iq:
        1-D complex IQ array (any complex dtype; cast to complex64
        internally). Typically the output of ``despread`` (one
        complex sample per symbol).

    Returns
    -------
    1-D ``np.int8`` array of bits in ``{0, 1}``, same length as
    ``iq``. Decision rule: ``Re{iq} >= 0 -> 0``, ``Re{iq} < 0 -> 1``.
    The exact-zero boundary maps to 0 deterministically so the
    round-trip ``bpsk_demodulate(bpsk_modulate(b)) == b`` holds for
    every valid input.
    """
    if iq.ndim != 1:
        msg = f"iq must be 1-D (got shape {iq.shape})."
        raise DsssError(msg)
    iq_c = np.asarray(iq, dtype=np.complex64)
    return np.where(iq_c.real >= 0.0, np.int8(0), np.int8(1))
