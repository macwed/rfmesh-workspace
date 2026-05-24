"""BPSK modulation / demodulation primitives (Iter 1).

BPSK is the v1.3.0 modulation choice for the DSSS link. Bits map to
``+1`` / ``-1`` symbols; symbols multiply the carrier IQ to produce a
real-valued I-only signal (Q = 0). The demodulator takes coherent IQ
after despreading and recovers bits by sign-checking the real part of
the integrated symbol energy.

Why BPSK and not QPSK / higher-order:

* BPSK has the most forgiving Eb/N0 for a given BER -- ~9.6 dB Eb/N0
  for 1e-3 BER under AWGN. Combined with the length-1023 spreading
  gain (~30 dB) the link tolerates extreme negative *channel* SNR
  while still hitting target BER.
* Symbol-rate / chip-rate ratio is 1:1 with the PN length -- no
  complication from constellation-mapping when the spreader is
  the dominant SNR mechanism.
* The pitch slide says "BPSK + length-1023 PN"; sticking to one
  modulation in v1.3.0 keeps the demo narrative tight.

ITER 0 SKELETON: docstring only. Iter 1 fills ``bpsk_modulate(bits)
-> IQBlock`` and ``bpsk_demodulate(iq) -> bits``, with a golden
artefact for the round-trip at SNR >= 20 dB.
"""

from __future__ import annotations
