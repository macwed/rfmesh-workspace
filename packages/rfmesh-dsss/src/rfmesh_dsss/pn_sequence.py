"""Maximal-length (m-sequence) PN generation via LFSR (Iter 1).

The DSSS spreading code is a length-``2**n - 1`` m-sequence generated
by a Fibonacci LFSR with primitive-polynomial taps. Default is
length-10 (1023 chips) per ``constants.DEFAULT_SPREADING_FACTOR`` /
``CommsConfig.spreading_factor``; the canonical tap set is
``(10, 3)`` (i.e. ``x^10 + x^3 + 1``).

KEY PROPERTIES THE GOLDEN TESTS PIN (Iter 1):

* **Length**: exactly ``2**n - 1`` non-repeating chips before the LFSR
  cycles.
* **Balance**: count(+1) - count(-1) == 1 (one more +1 than -1, the
  m-sequence's defining DC characteristic).
* **Two-level autocorrelation**: the cyclic autocorrelation is
  ``2**n - 1`` at zero lag (the peak) and exactly ``-1`` at every
  non-zero lag. This is *the* property that gives DSSS its processing
  gain: the despreader's correlator sees a sharp peak at code-phase
  alignment and uniform low sidelobes elsewhere, so a co-channel
  interferer that is not synced to the PN sequence is spread out
  flat by the despreader.
* **No fixed-point seed**: zero seed produces all-zero output (which
  is not an m-sequence); ``InvalidPnSequenceError`` raised.

ITER 0 SKELETON: this module ships docstring-only. Iter 1 fills the
LFSR primitive and ``generate_m_sequence``, with golden artefacts at
``tests/golden/pn_len10_seed1_taps10_3.npz`` (1023-chip sequence,
autocorrelation array, balance count).
"""

from __future__ import annotations
