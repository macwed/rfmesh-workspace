"""Matched-filter correlation + preamble acquisition.

The receiver does not know *when* a frame starts. The preamble (the
fixed alternating ``0101...`` bit pattern from ``framing``, pre-
spread by the same PN sequence as the rest of the frame) is the
synchronisation aid: the receiver continuously correlates incoming
chips against a known preamble + PN replica and declares "frame
starts here" when the correlator output exceeds a configured
threshold.

ALGORITHM

* **Matched filter**: FFT-based linear cross-correlation of received
  chips against the local replica via
  ``scipy.signal.fftconvolve``. ``O(N log N)`` instead of ``O(N^2)``
  is the difference between realtime acquisition and a slideware
  demo. Implementation: convolve received with the time-reversed
  conjugate replica.
* **Code-phase search**: the correlation peak's index gives the
  starting chip of the frame to within one chip. Sub-chip refinement
  comes in Iter 3 with SDR oversampling.
* **Detection threshold**: configurable ratio relative to the
  no-signal noise floor. The honest default is conservative -- a
  missed acquisition is cheaper than a false acquisition that hands
  the despreader garbage.

FAILURE MODES (raised, not silently swallowed -- B3)

* No correlation peak above ``threshold_ratio * median_magnitude``:
  ``AcquisitionFailedError`` with reason ``"no peak above threshold"``.
  Caller retries next slot or marks the link down.
* Ambiguous peaks (two or more within ``AMBIGUITY_RATIO`` of each
  other): ``AcquisitionFailedError`` with reason ``"ambiguous peaks"``.
  Distinct from the no-peak case so the operator sees the difference
  on the dashboard (multipath warning vs link-down warning).

ITER 2 SCOPE (this module)

* ``matched_filter(received, replica)`` -- FFT correlation,
  one-dimensional, returns correlation magnitudes (real ``float64``).
* ``acquire_preamble(received, replica, threshold_ratio,
  ambiguity_ratio)`` -- finds the start-of-frame chip index using
  the matched-filter peak, raises on no-peak or ambiguous-peak.

Iter 3 will add the SDR-edge wiring (continuous streaming, sliding
window, per-block acquisition latching). Iter 2 is block-mode
acquisition for the offline simulator.
"""

from __future__ import annotations

from typing import Final

import numpy as np
from scipy.signal import fftconvolve  # type: ignore[import-untyped]

from .exceptions import AcquisitionFailedError

#: Default detection threshold: peak magnitude must exceed this
#: multiple of the median correlation magnitude. A no-signal input
#: (pure complex Gaussian noise) has a peak/median ratio that grows
#: only as ``sqrt(2 * log(N))``; for ``N`` in the thousands that is
#: ~5-7. The default 8.0 sits just above that band so noise-only
#: acquisitions reliably refuse.
DEFAULT_THRESHOLD_RATIO: Final[float] = 8.0

#: Default ambiguity ratio: the second-highest peak must be at least
#: this fraction smaller than the top peak for the acquisition to be
#: declared unambiguous. 0.7 catches strong multipath echoes (which
#: typically arrive within a few dB of the line-of-sight peak) while
#: tolerating the autocorrelation sidelobes of the alternating
#: preamble (which sit well below the main lobe).
DEFAULT_AMBIGUITY_RATIO: Final[float] = 0.7


def matched_filter(received: np.ndarray, replica: np.ndarray) -> np.ndarray:
    """Cross-correlate ``received`` against ``replica`` via FFT.

    Returns the magnitude of the complex cross-correlation output:
    ``|received ⋆ replica*|``. Length: ``len(received) + len(replica) - 1``
    (linear, not cyclic -- avoids the ambiguity a cyclic correlator
    would introduce when the preamble is shorter than the search
    window).

    Parameters
    ----------
    received:
        1-D complex IQ chips at one sample per chip (Iter 2 scope;
        Iter 3 SDR integration handles oversampling).
    replica:
        1-D complex IQ chips of the known preamble (already
        pre-spread by the PN sequence). Must be shorter than or
        equal in length to ``received``.

    Returns
    -------
    1-D ``np.float64`` correlation-magnitude array of length
    ``len(received) + len(replica) - 1``. Index ``i`` corresponds to
    the alignment where the first chip of the replica overlays
    chip ``i - (len(replica) - 1)`` of the received stream.
    """
    if received.ndim != 1:
        msg = f"received must be 1-D (got shape {received.shape})."
        raise AcquisitionFailedError(msg)
    if replica.ndim != 1:
        msg = f"replica must be 1-D (got shape {replica.shape})."
        raise AcquisitionFailedError(msg)
    if replica.size == 0:
        msg = "replica must be non-empty."
        raise AcquisitionFailedError(msg)
    if received.size < replica.size:
        msg = (
            f"received ({received.size}) must be at least as long as "
            f"replica ({replica.size}) for a meaningful correlation."
        )
        raise AcquisitionFailedError(msg)
    rec = np.asarray(received, dtype=np.complex64)
    rep = np.asarray(replica, dtype=np.complex64)
    # Cross-correlation = convolution with the time-reversed conjugate.
    corr = fftconvolve(rec, np.conj(rep)[::-1], mode="full")
    magnitude: np.ndarray = np.abs(corr).astype(np.float64)
    return magnitude


def acquire_preamble(
    received: np.ndarray,
    replica: np.ndarray,
    threshold_ratio: float = DEFAULT_THRESHOLD_RATIO,
    ambiguity_ratio: float = DEFAULT_AMBIGUITY_RATIO,
) -> int:
    """Find the start-of-frame chip index in ``received``.

    Computes the matched-filter magnitude, then declares acquisition
    iff a single peak is both above the threshold and clearly the
    largest. Returns the chip index in ``received`` at which the
    replica begins to align (i.e. the start of the preamble in the
    received stream).

    Parameters
    ----------
    received, replica:
        See ``matched_filter``.
    threshold_ratio:
        Peak magnitude must exceed
        ``threshold_ratio * median(correlation_magnitudes)`` for
        acquisition to succeed. Default ``DEFAULT_THRESHOLD_RATIO``.
    ambiguity_ratio:
        The second-highest peak (after excluding samples within one
        replica-length of the main peak) must be below
        ``ambiguity_ratio * top_peak`` for the acquisition to be
        declared unambiguous. Default ``DEFAULT_AMBIGUITY_RATIO``.

    Returns
    -------
    Integer chip index in ``received`` at which the preamble begins.

    Raises
    ------
    AcquisitionFailedError:
        No peak above threshold (``"no peak above threshold"``) OR
        ambiguous peaks (``"ambiguous peaks"``). The reason string
        is the operator-facing cause; the comms loop's dashboard
        surface distinguishes a link-down warning from a multipath
        warning by branching on it.
    """
    if threshold_ratio <= 0.0:
        msg = f"threshold_ratio must be > 0 (got {threshold_ratio})."
        raise AcquisitionFailedError(msg)
    if not 0.0 < ambiguity_ratio < 1.0:
        msg = f"ambiguity_ratio must be in (0, 1) (got {ambiguity_ratio})."
        raise AcquisitionFailedError(msg)
    corr_mag = matched_filter(received, replica)
    peak_index = int(np.argmax(corr_mag))
    peak_value = float(corr_mag[peak_index])
    noise_floor = float(np.median(corr_mag))
    # Avoid divide-by-zero on the pathological all-zero input.
    if noise_floor <= 0.0:
        msg = "correlation noise floor is zero -- no signal in window."
        raise AcquisitionFailedError(msg)
    if peak_value < threshold_ratio * noise_floor:
        msg = (
            f"no peak above threshold "
            f"(peak={peak_value:.3g}, threshold="
            f"{threshold_ratio * noise_floor:.3g})."
        )
        raise AcquisitionFailedError(msg)
    # Exclude a guard band around the main peak (one replica-length
    # to each side; values inside the main lobe are not "other peaks"
    # for ambiguity purposes).
    guard = replica.size
    mask = np.ones_like(corr_mag, dtype=bool)
    mask[max(0, peak_index - guard) : peak_index + guard + 1] = False
    if mask.any():
        runner_up = float(corr_mag[mask].max())
        if runner_up > ambiguity_ratio * peak_value:
            msg = (
                f"ambiguous peaks "
                f"(top={peak_value:.3g}, runner_up={runner_up:.3g}, "
                f"ratio={runner_up / peak_value:.2f} > "
                f"{ambiguity_ratio:.2f})."
            )
            raise AcquisitionFailedError(msg)
    # Translate from full-correlation index to chip index in
    # ``received``: the convolution output index ``i`` corresponds to
    # replica-aligned-at chip ``i - (replica.size - 1)`` of received.
    chip_index = peak_index - (replica.size - 1)
    # If acquisition fires before the first full overlap (chip_index
    # < 0), reject -- a real preamble cannot start before the buffer.
    if chip_index < 0:
        msg = (
            f"peak at convolution index {peak_index} maps to negative "
            f"chip index {chip_index}; preamble cannot start before the buffer."
        )
        raise AcquisitionFailedError(msg)
    return chip_index
