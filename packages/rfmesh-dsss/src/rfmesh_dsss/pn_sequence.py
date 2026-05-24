"""Maximal-length (m-sequence) PN generation via Fibonacci LFSR.

The DSSS spreading code is a length-``2**n - 1`` m-sequence generated
by a Fibonacci LFSR with primitive-polynomial taps. Default is
length-10 (1023 chips) per ``constants.DEFAULT_SPREADING_FACTOR`` /
``CommsConfig.spreading_factor``; the canonical tap set is
``(10, 3)`` (i.e. ``x**10 + x**3 + 1``).

KEY PROPERTIES THE GOLDEN TESTS PIN:

* **Length**: exactly ``2**n - 1`` non-repeating chips before the LFSR
  cycles.
* **Balance**: ``count(+1) - count(-1) == 1`` (one more +1 than -1 --
  the m-sequence's defining DC characteristic; in {0, 1} domain there
  are ``2**(n-1)`` ones and ``2**(n-1) - 1`` zeros, and the
  ``1 -> +1`` / ``0 -> -1`` BPSK mapping preserves the imbalance).
* **Two-level autocorrelation**: the cyclic autocorrelation is
  ``2**n - 1`` at zero lag (the peak) and exactly ``-1`` at every
  non-zero lag. This is *the* property that gives DSSS its
  processing gain: the despreader's correlator sees a sharp peak at
  code-phase alignment and uniform low sidelobes elsewhere, so a
  co-channel interferer that is not synced to the PN sequence is
  spread out flat by the despreader.
* **No fixed-point seed**: zero seed produces all-zero output (which
  is not an m-sequence); ``InvalidPnSequenceError`` is raised.

CONVENTION (binding for downstream callers)

``taps`` are **polynomial powers** of the primitive polynomial
``p(x) = x^n + x^{k_1} + x^{k_2} + ... + 1`` that defines the
m-sequence. The tuple lists the powers from the polynomial in any
order, **including** the leading power ``n`` (which equals
``register_length`` and bounds the LFSR width) and **excluding** the
implicit constant ``+ 1`` (always present in a primitive polynomial
over GF(2) and always XOR'd into the feedback).

Example: the canonical length-10 polynomial ``x^10 + x^3 + 1`` is
encoded as ``taps = (10, 3)``.

Implementation -- Fibonacci LFSR with the standard
``s_{k+n} = sum_{i: c_i = 1, i < n} s_{k+i}`` recurrence. The register
holds an ``n``-bit integer; the output chip on each step is the
**least-significant bit** (LSB), interpreted as ``s_k`` (the oldest
sample in the window). The feedback bit ``s_{k+n}`` is computed as
the XOR of register bits at positions ``{i : c_i = 1, 0 <= i < n}``;
the register shifts right and the feedback bit enters the most-
significant position. Because ``c_0 = 1`` for any primitive
polynomial, bit 0 (the LSB / output) is *always* included in the
feedback XOR -- the caller does not specify ``0`` in ``taps``; it is
implicit and adding it explicitly would be a doubled XOR (validator
rejects ``min(taps) < 1``).

BPSK mapping: the raw LFSR output is in ``{0, 1}``. ``generate_m_sequence``
maps ``1 -> +1`` and ``0 -> -1`` and returns ``np.int8``. Storing
chips as ``int8`` (not ``float32``) keeps the 1023-chip array a tiny
1 KiB so golden artefacts stay small.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .exceptions import InvalidPnSequenceError

#: Smallest LFSR register length that produces a non-degenerate
#: m-sequence (length-1 has only one state and cannot cycle;
#: length-2 gives the 3-chip sequence ``+1, +1, -1``).
_MIN_REGISTER_LENGTH: int = 2


def _validate_lfsr_params(
    register_length: int,
    taps: Sequence[int],
    seed: int,
) -> None:
    """Raise ``InvalidPnSequenceError`` on any structurally-invalid input.

    Defensive layer mirroring the validators on ``CommsConfig`` so
    callers that bypass the config (e.g. test code constructing PN
    directly) still get the same rejection.
    """
    if register_length < _MIN_REGISTER_LENGTH:
        msg = (
            f"register_length must be >= {_MIN_REGISTER_LENGTH} "
            f"(got {register_length})."
        )
        raise InvalidPnSequenceError(msg)
    if not taps:
        msg = "taps must be non-empty."
        raise InvalidPnSequenceError(msg)
    if max(taps) != register_length:
        msg = (
            f"max(taps) must equal register_length "
            f"(got max(taps)={max(taps)}, register_length={register_length})."
        )
        raise InvalidPnSequenceError(msg)
    if min(taps) < 1:
        msg = f"taps are 1-indexed; min(taps) must be >= 1 (got {min(taps)})."
        raise InvalidPnSequenceError(msg)
    if len(set(taps)) != len(taps):
        msg = f"taps must be unique (got {taps!r})."
        raise InvalidPnSequenceError(msg)
    if seed == 0:
        msg = (
            "seed must be non-zero -- a zero seed is a fixed-point of any "
            "Fibonacci LFSR (produces all-zero output, which is not an "
            "m-sequence)."
        )
        raise InvalidPnSequenceError(msg)
    if seed < 0 or seed >= (1 << register_length):
        msg = (
            f"seed must be in [1, 2**register_length) "
            f"= [1, {1 << register_length}) (got {seed})."
        )
        raise InvalidPnSequenceError(msg)


def generate_m_sequence(
    register_length: int,
    taps: Sequence[int],
    seed: int,
) -> np.ndarray:
    """Generate one full period of an m-sequence as BPSK +/-1 chips.

    Parameters
    ----------
    register_length:
        LFSR register width ``n``. The sequence is ``2**n - 1`` chips.
    taps:
        1-indexed feedback-tap positions. ``max(taps)`` must equal
        ``register_length``; the smallest tap must be >= 1. The tap
        tuple must correspond to a primitive polynomial for the
        output to actually be maximal-length; this function does NOT
        validate primitivity (an unbounded check) -- callers either
        pass a known-good tap set (see
        ``constants.DEFAULT_LFSR_TAPS_LEN10``) or verify length /
        autocorrelation properties of the output downstream.
    seed:
        Non-zero initial register state, in ``[1, 2**n)``. Different
        seeds produce cyclically-shifted versions of the same
        sequence (the same m-sequence starts at a different
        code-phase).

    Returns
    -------
    ``np.ndarray`` of ``dtype=int8`` and length ``2**n - 1``, with
    each element in ``{+1, -1}``.

    Raises
    ------
    InvalidPnSequenceError:
        Any of the structural checks in ``_validate_lfsr_params``
        fails.
    """
    _validate_lfsr_params(register_length, taps, seed)
    length = (1 << register_length) - 1
    bits = np.empty(length, dtype=np.int8)
    state = int(seed)
    msb_shift = register_length - 1
    # Feedback bit positions in the register (0-indexed from LSB):
    # every polynomial power in [0, n) where the polynomial coefficient
    # is 1. Intermediate powers come from ``taps`` (excluding the
    # leading ``n`` itself); position 0 (the LSB) is always included
    # because ``c_0 = 1`` for any primitive polynomial.
    intermediate = (int(t) for t in taps if int(t) < register_length)
    feedback_bit_positions = tuple(sorted({*intermediate, 0}))
    for i in range(length):
        bits[i] = state & 1
        feedback = 0
        for pos in feedback_bit_positions:
            feedback ^= (state >> pos) & 1
        state = (state >> 1) | (feedback << msb_shift)
    # BPSK map: 1 -> +1, 0 -> -1. Gives count(+1) - count(-1) = +1.
    return np.where(bits == 1, np.int8(1), np.int8(-1))


def cyclic_autocorrelation(sequence: np.ndarray) -> np.ndarray:
    """Compute the cyclic autocorrelation of a +/-1 BPSK chip sequence.

    For a length-``N`` m-sequence the output is ``[N, -1, -1, ..., -1]``
    -- a sharp peak at lag 0 and uniform ``-1`` sidelobes (the "two-
    level autocorrelation" property; see module docstring). The
    function is FFT-based for ``O(N log N)`` speed; the golden test
    pins exact integer values, so the inverse-FFT result is rounded
    back to ``int64``.
    """
    if sequence.ndim != 1:
        msg = f"sequence must be 1-D (got shape {sequence.shape})."
        raise InvalidPnSequenceError(msg)
    if sequence.size == 0:
        msg = "sequence must be non-empty."
        raise InvalidPnSequenceError(msg)
    spec = np.fft.fft(sequence.astype(np.float64))
    power = spec * np.conj(spec)
    auto = np.fft.ifft(power).real
    return np.rint(auto).astype(np.int64)
