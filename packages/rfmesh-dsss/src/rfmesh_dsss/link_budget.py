"""Link-budget honesty: processing gain, theoretical BER, measured BER bands (Iter 1).

The DSSS link's claimed processing gain is a load-bearing pitch
number; it is also easy to lie about. This module computes the
predicted BER under AWGN from textbook expressions and gives
``test_ber_honesty`` (Iter 2) the prediction band to validate
Monte-Carlo measured BER against (analog of ``rfmesh-dsp``'s
sigma-honesty test for L1/L2 estimators -- see ``packages/rfmesh-dsp
/tests/test_sigma_honesty.py`` for the pattern).

CORE EXPRESSIONS

* **Processing gain**: ``PG_dB = 10 * log10(spreading_factor)``.
  For length-1023: ``~30.10 dB``. Always pinned exact; the test
  catches any off-by-one in chip counting.
* **BPSK BER under AWGN**: ``Pb = 0.5 * erfc(sqrt(Eb / N0))``.
  ``Eb/N0`` in linear units. The despread output's effective
  Eb/N0 = channel SNR + PG, so a channel SNR of -10 dB gives
  Eb/N0 ~= +20 dB after spreading, yielding BER ~= 4e-6 (very
  good).
* **Honest band**: measured BER must lie within ``+/-20 %`` of
  the textbook prediction over SNR points ``{10, 20, 30} dB`` for
  the link-honesty test to pass. Same band as ``rfmesh-dsp``'s
  sigma-honesty test (B2 surface). Below the band: link is
  poisoning itself with implementation bugs (over-claimed PG,
  bias in despreader). Above the band: simulator is too kind --
  also a B3 violation, calibration of the channel model is off.

ITER 0 SKELETON: docstring only. Iter 1 fills
``processing_gain_db(spreading_factor) -> float`` and
``ber_theoretical_bpsk(eb_n0_linear) -> float``. Iter 2 wires the
honesty Monte-Carlo (``tests/test_ber_honesty.py``) that runs
synthetic frames through the loopback channel at three SNR points
and asserts the +/-20 % band on measured BER vs prediction.

NO ABSOLUTE-POWER UNITS HERE

In line with ARCHITECTURE.md Appendix B.2 / INTERFACES.md §0:
``rfmesh-dsss`` does not deal in dBm. All link calculations are in
relative terms (Eb/N0, channel SNR above noise floor, PG).
``TransmitterCapabilities.max_tx_power_normalized`` is the honest
unit the SDR side exposes; the operator-tuned scenario picks an
operating point inside ``[0.0, 1.0]`` and the link reports
*relative margin* on the dashboard, never dBm or EIRP.
"""

from __future__ import annotations
