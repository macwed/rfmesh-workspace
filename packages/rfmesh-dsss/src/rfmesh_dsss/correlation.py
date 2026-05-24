"""Matched-filter correlation + preamble acquisition (Iter 2).

The receiver does not know *when* a frame starts. The preamble (a
fixed, known sequence of BPSK symbols pre-spread by the same PN
sequence as the rest of the frame) is the synchronisation aid: the
receiver continuously correlates incoming chips against the known
preamble + PN replica and declares "frame starts here" when the
correlator output exceeds a configured threshold.

ALGORITHM (Iter 2):

* **Matched filter**: FFT-based circular correlation of received
  chips against the local preamble + PN replica via
  ``scipy.signal.fftconvolve``. ``O(N log N)`` instead of ``O(N^2)``
  is the difference between realtime acquisition and a slideware
  demo.
* **Code-phase search**: the correlation peak's index gives the
  starting chip of the frame to within one chip. Refinement to
  sub-chip resolution is handled by ``timing.recover_chip_timing``
  in the second sync stage.
* **Detection threshold**: configurable Pfa (false-alarm
  probability). Set conservatively because a missed detection is
  cheaper than a false acquisition that sends the despreader
  syncing on garbage.

FAILURE MODES (raised, not silently swallowed -- B3):

* No correlation peak above threshold: ``AcquisitionFailedError``,
  caller retries next slot or marks link down.
* Ambiguous peaks (multiple within small ratio): same exception,
  distinct cause string -- caller treats as a multipath warning
  for the operator.

ITER 0 SKELETON: docstring only. Iter 2 fills ``matched_filter``
and ``acquire_preamble``; Iter 2 golden tests include: preamble in
clean AWGN at SNR {10, 20, 30} dB, preamble through
``rfmesh-sdr.simulator.multipath_fir`` (mirror of L1 golden suite
choosing the same channel model), and acquisition refusal at
SNR < 0 dB (link below threshold).
"""

from __future__ import annotations
