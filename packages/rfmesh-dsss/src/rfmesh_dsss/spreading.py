"""Spread / despread primitives (Iter 1).

The spreader takes a stream of BPSK symbols (``+1`` / ``-1``) and
multiplies each by the full 1023-chip PN sequence -- one symbol
becomes 1023 chips. The despreader does the inverse: it integrates
chip-by-chip product against the *same* PN sequence over one symbol's
worth of chips and outputs the sign of the integrated correlation.

THE 30 dB OF PROCESSING GAIN

A co-channel interferer that is not synced to the PN sequence sees
its energy spread over 1023 frequency bins by the despreader, while
the wanted symbol's energy accumulates coherently. Ratio of wanted to
interferer at the output is the input ratio times 1023, i.e. +30.1 dB
gain. This is why the link operates well below the channel SNR a
bare BPSK would need; it is also why ``link_budget`` reports BER
against *Eb/N0* (post-despread energy) rather than channel SNR.

ITER 0 SKELETON: docstring only. Iter 1 fills ``spread(symbols,
pn) -> chips`` and ``despread(chips, pn) -> symbols``, with
golden artefacts covering: clean round-trip, round-trip with
single co-channel interferer (verifies +30 dB SNR jump), and
round-trip with PN-out-of-sync (verifies despread output is noise
when code-phase is wrong).
"""

from __future__ import annotations
