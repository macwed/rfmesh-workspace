"""Constants for the rfmesh-dsss DSP module.

Kept deliberately small: anything that can be a function argument or a
CommsConfig field should be one, not a module-level constant.
"""

from __future__ import annotations

from typing import Final

#: Numerical-stability epsilon for any inverse / log / divide where the
#: denominator could approach zero on degenerate input. Mirrors the
#: same constant in ``rfmesh_dsp.constants`` -- intentionally identical
#: so cross-package numeric thresholds align.
EPSILON: Final[float] = 1e-12

#: Canonical default spreading factor: a length-10 LFSR produces a
#: maximal m-sequence of 2**10 - 1 = 1023 chips. Processing gain
#: 10*log10(1023) ~= 30.1 dB. Used as the default in
#: ``CommsConfig.spreading_factor`` (frozen-contract level) and as the
#: parameter the link-budget honesty test runs Monte-Carlo against.
DEFAULT_SPREADING_FACTOR: Final[int] = 1023

#: Canonical primitive polynomial for length-10 m-sequence:
#: ``x^10 + x^3 + 1`` -> taps ``(10, 3)`` in the 1-indexed
#: ``CommsConfig.lfsr_taps`` convention. The set of valid taps for
#: every length is enumerated in ``pn_sequence`` (Iter 1).
DEFAULT_LFSR_TAPS_LEN10: Final[tuple[int, int]] = (10, 3)
