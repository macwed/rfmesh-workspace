"""Spread / despread primitives for the DSSS link.

The spreader multiplies each BPSK symbol by the full PN sequence,
turning ``len(symbols)`` symbols into ``len(symbols) * spreading_factor``
chips. The despreader does the inverse: it integrates chip-by-chip
product against the same PN sequence over one symbol's worth of
chips and outputs one complex sample per symbol (the matched-filter
output for a synchronised PN replica).

THE 30 dB OF PROCESSING GAIN

A co-channel interferer not synced to the PN sequence has its
energy spread over ``spreading_factor`` frequency bins by the
despreader, while the wanted symbol's energy accumulates
coherently. Output wanted-to-interferer ratio is the input ratio
times ``spreading_factor``, i.e. ``+10 * log10(1023) ~= +30.10 dB``
gain for length-1023. See ``link_budget.processing_gain_db``.

ASSUMPTIONS THIS MODULE DOES *NOT* MAKE

* It does NOT handle code-phase acquisition (that is ``correlation``,
  Iter 2). ``despread`` assumes the PN replica is already aligned
  with the incoming chips at chip 0; chip-level alignment is the
  upstream module's job.
* It does NOT handle chip-rate / sample-rate mismatch. Inputs are
  assumed to be at one sample per chip. Pulse-shaping and
  oversampling come in with the realtime SDR integration (Iter 3+).
* It does NOT correct carrier phase (``timing.correct_carrier_phase``,
  Iter 2). A constant complex phase rotation between TX and RX
  rotates the despread output uniformly; the downstream BPSK
  demodulator's sign-of-real-part decision is then degraded.

CONVENTION

* ``spread(symbols, pn)``: symbols shape ``(M,)`` complex, pn shape
  ``(N,)`` int8 with ``N == spreading_factor``. Output shape
  ``(M * N,)`` complex. Vectorised via outer-product + flatten --
  no Python-level chip loop, so 1023-chip spread of 1000 symbols is
  one numpy expression.
* ``despread(chips, pn)``: chips shape ``(M * N,)`` complex, pn
  shape ``(N,)``. Output shape ``(M,)`` complex -- one matched-
  filter output per symbol, divided by ``N`` (normalised correlator)
  so a clean ``+1`` symbol round-trips to ``+1 + 0j``.
"""

from __future__ import annotations

import numpy as np

from .exceptions import DsssError


def spread(symbols: np.ndarray, pn: np.ndarray) -> np.ndarray:
    """Multiply each symbol by the full PN sequence; flatten to chips.

    Parameters
    ----------
    symbols:
        1-D complex symbol array; typically the output of
        ``bpsk_modulate``.
    pn:
        1-D PN chip sequence (``+1`` / ``-1``); typically the output
        of ``generate_m_sequence``. Length equals the spreading
        factor.

    Returns
    -------
    1-D complex64 chip array of length ``len(symbols) * len(pn)``.
    Symbol ``i`` occupies chip indices ``i * len(pn)`` through
    ``(i + 1) * len(pn) - 1``.
    """
    if symbols.ndim != 1:
        msg = f"symbols must be 1-D (got shape {symbols.shape})."
        raise DsssError(msg)
    if pn.ndim != 1:
        msg = f"pn must be 1-D (got shape {pn.shape})."
        raise DsssError(msg)
    if pn.size == 0:
        msg = "pn must be non-empty."
        raise DsssError(msg)
    sym_c = np.asarray(symbols, dtype=np.complex64)
    pn_c = np.asarray(pn, dtype=np.complex64)
    # Outer product (M, N) then flatten: each row is symbol_i * pn.
    return np.outer(sym_c, pn_c).reshape(-1).astype(np.complex64)


def despread(chips: np.ndarray, pn: np.ndarray) -> np.ndarray:
    """Correlate chips against the PN replica, one symbol at a time.

    Parameters
    ----------
    chips:
        1-D complex chip array. Length must be a multiple of
        ``len(pn)``.
    pn:
        1-D PN chip sequence the spreader used. Must match the
        spreader's sequence at the chip level for the correlator to
        produce coherent output.

    Returns
    -------
    1-D complex64 symbol array of length ``len(chips) // len(pn)``.
    Each output sample is ``sum(chips_block * pn) / len(pn)`` -- the
    normalised correlator output. Clean ``+1`` symbol -> ``+1 + 0j``;
    clean ``-1`` symbol -> ``-1 + 0j``.

    Raises
    ------
    DsssError:
        ``len(chips) % len(pn) != 0`` -- partial-symbol input is a
        sync / framing bug and must surface, not be silently
        truncated.
    """
    if chips.ndim != 1:
        msg = f"chips must be 1-D (got shape {chips.shape})."
        raise DsssError(msg)
    if pn.ndim != 1:
        msg = f"pn must be 1-D (got shape {pn.shape})."
        raise DsssError(msg)
    if pn.size == 0:
        msg = "pn must be non-empty."
        raise DsssError(msg)
    if chips.size % pn.size != 0:
        msg = (
            f"chips length ({chips.size}) must be a multiple of pn length "
            f"({pn.size}); partial-symbol input is a sync/framing bug."
        )
        raise DsssError(msg)
    n_symbols = chips.size // pn.size
    chips_c = np.asarray(chips, dtype=np.complex64).reshape(n_symbols, pn.size)
    pn_c = np.asarray(pn, dtype=np.complex64)
    # Per-symbol correlator: dot product chips_block . pn, normalised by N.
    correlated = chips_c @ pn_c
    return (correlated / np.float32(pn.size)).astype(np.complex64)
