"""Chip / symbol timing recovery and carrier-phase correction (Iter 2).

The matched filter in ``correlation`` locates the frame start to
within one chip. ``timing`` does the second-stage refinement to
sub-chip accuracy and tracks slow drift across a long frame:

* **Early-late gate**: classic discrete-time PLL. At each symbol
  boundary the despreader correlates two extra slightly-offset
  copies (``early`` and ``late``); the difference is the timing
  error and drives a first-order loop filter that nudges the
  sampling instant.
* **Carrier-phase correction**: BPSK is sensitive to a static
  phase offset between the local oscillator and the transmitter
  (a constant rotation in the complex plane). Tracked by a Costas
  loop on the despread symbols.
* **CFO** (carrier-frequency offset): handled crudely in v1.3.0 by
  the same Costas loop integrating; if Iter 3 hardware testing
  shows the offset is large enough to wrap during one frame, a
  separate pre-correlation FFT-based CFO estimate becomes Iter 4
  scope.

ITER 0 SKELETON: docstring only. Iter 2 fills
``recover_chip_timing`` and ``correct_carrier_phase``, with golden
artefacts: clean timing recovery (zero CFO), recovery under a
small CFO (~0.1 of the symbol rate), and refusal under a CFO
larger than the loop bandwidth can lock to.
"""

from __future__ import annotations
