"""Rules-based modulation-class classifier core.

Pure numpy. Maps a feature vector (see ``features.py``) to a
``(modulation_class, confidence)`` pair via four family scores, an
argmax + margin selector, and a confidence floor that short-circuits to
``"unknown"`` (Invariant B3 honesty: no fabricated labels at low SNR).

Thresholds and the confidence floor are *empirically tuned* against the
WS-B-005 synthetic-IQ test scenarios at SNR in {5, 10, 20} dB. The
tuning provenance is recorded next to each constant; future
modulation families added by WS-B-006 may push these values, and the
recorded comments keep the choice auditable -- the same honesty
discipline the WS-B sigma-thresholds use.

The discriminators each family relies on
----------------------------------------

The signal-design view:

* **CW** -- a single tone. The IF histogram has one dominant bin
  (``if_peaks == 1``), the IF-histogram entropy is low (~1 nat at
  SNR=20 dB, ~2.5 nats at SNR=5 dB after noise smears the bin), and
  the per-segment STFT peak-bin sequence is constant
  (``peak_bin_std ~ 0``). The chirp / hop detectors see no signal
  (``chirp_span ~ 0``, ``hop_prominence ~ 0``).
* **FHSS** -- many distinct frequencies, each visited briefly. The
  IF histogram has several peaks (``if_peaks >= 3`` typically 5-8 in
  the WS-B-005 fixtures), the IF-histogram entropy is high (~2.5-3.3
  nats), and the per-segment STFT peak-bin sequence jumps widely
  (``peak_bin_std`` ~ 40-50 for the 8-bin fixture).
* **FSK** -- a *narrow* constant-deviation FM signal. The histogram
  has 1-2 peaks (the deviation may be smaller than the histogram bin
  width), the chirp-detector picks up a small ``chirp_span``
  (~ deviation x 2), and the per-segment peak-bin sequence flips
  between just two values (``peak_bin_std`` ~ a few bins).
* **LoRa** -- a linear chirp. The chirp-detector fits a clean line:
  small ``chirp_residual_rms / chirp_span``, wide ``chirp_span``
  (~ symbol bandwidth), moderate ``peak_bin_std``.

Noise has high entropy, ``if_peaks ~ 12`` (the histogram is roughly
uniform), and ``peak_bin_std`` ~ 70+ (peak bin jumps across the entire
band each segment) -- so noise's "fingerprint" overlaps with FHSS's
in the IF-entropy axis but is distinguishable by ``peak_bin_std``
being *higher* than even FHSS, and by the hop-prominence being a
spurious-autocorrelation artefact rather than a real periodic
pattern. The FHSS score therefore caps on ``peak_bin_std`` so noise
does not register.

Tuning note for future maintainers: the rules-based core does not need
to be calibrated against ground-truth probabilities. The honesty
constraint is that the ``"unknown"`` output appear whenever the
classifier is not confident enough to assign a label. The empirical
test gate (12 high-SNR positives + 4 low-SNR ``"unknown"`` + 1 pure-
noise) is the auditable proof; if a tuning change breaks one of those
tests, the *test* is right and the threshold is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

ModulationLabel = Literal["cw", "fhss", "fsk", "lora", "unknown"]
"""The five-element output space of the classifier. ``"unknown"`` is
the honesty fallback (Invariant B3) and is *not* a fabrication of an
``UNKNOWN`` label -- the rules scorer returns it only when no family
score clears ``CONFIDENCE_FLOOR``."""


@dataclass(frozen=True, slots=True)
class FamilyScores:
    """Per-family scoring breakdown.

    Exposed as a return value so the test suite (and the eventual
    ops-dashboard "why did the classifier say that" panel) can inspect
    the four raw scores without re-running feature extraction. Not part
    of the public ``ClassificationResult`` -- that one only carries
    the final label, confidence, and the raw feature vector.
    """

    cw: float
    fhss: float
    fsk: float
    lora: float


# ---------------------------------------------------------------------------
# Empirically tuned thresholds. Each constant carries the test scenario it
# was calibrated against. Adjusting one requires re-running the WS-B-005
# acceptance suite (21 tests in test_modulation_classifier.py).
#
# Calibration scenarios used (from tests/conftest.py at seed=42, fs=1 MS/s,
# N=8192 samples; see .claude/scratchpad/_ws_b_005_probe.py for the
# diagnostic dump). Approximate feature values for each scenario:
#
#                  | CW(20)| CW(5) | FHSS  | FSK   | LoRa  | Noise |
#  if_peaks        |   1   |   1   |  5-8  |  1-2  |   1   |  ~12  |
#  if_entropy      |   0.9 |   2.6 |   3.0 |   1.5 |   2.0 |   3.5 |
#  peak_bin_std    |   0   |   0   |   45  |  ~6   |   9   |  ~73  |
#  chirp_span_hz   |   0   |   0   |  500k |  47k  | 113k  |  ~900k|
#  chirp_resid/    |   -   |   -   |  0.35 |  0.48 |  0.31 |  ~0.3 |
#    chirp_span    |       |       |       |       |       |       |
#  hop_prominence  |   0   |   0   |  0.55 |  1.0  |  1.0  |  1.0  |
#  flatness        |  1.0  |  0.93 |  0.97 |  0.97 |  0.98 |  0.84 |
# ---------------------------------------------------------------------------

CONFIDENCE_FLOOR: float = 0.5
"""Below this threshold the rules core returns ``"unknown"``.

Tuned at 0.5 against the WS-B-005 synthetic IQ. The honest positives
at SNR=5 dB all score >= 0.70 with the per-family weights below; the
low-SNR (-5 dB) scenarios + pure noise all score <= 0.45 across every
family. The 0.50 floor sits in the middle of that gap with comfortable
margin both ways.
"""

# ---- CW thresholds ---------------------------------------------------------
_CW_IF_PEAKS_TARGET: float = 1.0
"""CW signature: exactly one IF-histogram peak. The score peaks at 1
and falls off with a Gaussian of sigma=0.8 for ``if_peaks in {0, 2}``."""

_CW_IF_PEAKS_SIGMA: float = 0.8

_CW_PEAK_BIN_STD_MAX: float = 1.5
"""Tight CW signature: the per-segment STFT peak bin should be
constant. CW at SNR=20 dB has ``peak_bin_std == 0.0`` exactly; at
SNR=5 dB it stays at 0.0 (the noise floor of the spectrogram has not
dethroned the tone bin). 1.5 admits a one-bin flutter; above that the
signal is not a CW tone."""

_CW_PEAK_BIN_STD_SHARPNESS: float = 3.0
"""Sigmoid sharpness for the CW peak-bin-std gate."""

# ---- FHSS thresholds -------------------------------------------------------
# Historical note: an ``if_peaks`` floor was considered but dropped because
# at SNR=10 dB the 8-bin FHSS fixture's histogram collapses to ~2 peaks
# (the noise smears non-dominant hops); the peak_bin_std band-pass is a
# more robust discriminator.

_FHSS_PEAK_BIN_STD_FLOOR: float = 20.0
"""Per-segment STFT peak-bin standard deviation floor. FHSS at all
SNRs has ``peak_bin_std ~ 45`` (large hop set, peak bin lands far
apart between hops). FSK has ``peak_bin_std ~ 6``, LoRa ~ 9, CW = 0.
The floor at 20 cleanly separates FHSS from the constant-envelope
modulations."""

_FHSS_PEAK_BIN_STD_CAP: float = 60.0
"""Per-segment STFT peak-bin standard deviation cap. Pure noise has
``peak_bin_std ~ 73`` (peak bin jumps across the entire band each
segment). The cap at 60 means the FHSS score falls off above that --
honesty rule: noise must classify as ``"unknown"``, not as FHSS."""

_FHSS_PEAK_BIN_STD_SHARPNESS: float = 0.25
"""Sigmoid sharpness for the FHSS peak-bin-std gates. The pair (floor,
cap, sharpness) defines a smooth peaked region between 20 and 60."""

_FHSS_HOP_PROMINENCE_FLOOR: float = 0.3
"""Companion gate on hop-rate autocorrelation prominence. FHSS at
SNR=5 dB has ``hop_prominence = 0.55``; noise has spurious 1.0. The
floor at 0.3 admits the fixture but does not over-weight noise (which
fails the peak-bin-std cap regardless)."""

# ---- FSK thresholds --------------------------------------------------------
_FSK_PEAK_COUNT_TARGET: float = 1.5
"""FSK signature: 1 or 2 IF-histogram peaks (the histogram bin width
in the WS-B-005 fixture is ~31 kHz, the FSK deviation is +/- 25 kHz,
so the two peaks may merge into one). Target at 1.5 with sigma=1.5
gives a wide-and-flat plateau across peak_count in {1, 2, 3} which is
where FSK falls under noise. Combined with the chirp_span band-pass
gate, this is the FSK discriminator."""

_FSK_PEAK_COUNT_SIGMA: float = 1.5

_FSK_PEAK_BIN_STD_TARGET: float = 6.0
"""FSK signature: the per-segment peak bin flips between two close
values; the std is small but non-zero. The Gaussian is centred on
6 (the SNR=20 dB fixture value) with sigma=6, which means CW
(``peak_bin_std = 0``) scores ~ 0.61 -- but CW also has zero
``chirp_span`` and so the FSK score's chirp-span factor zeros it
out. LoRa (``peak_bin_std = 9``) scores ~ 0.88 here but loses on the
chirp-span / residual factor."""

_FSK_PEAK_BIN_STD_SIGMA: float = 6.0

_FSK_CHIRP_SPAN_MIN_HZ: float = 5_000.0
"""FSK minimum chirp span. Below 5 kHz the signal is too narrow to be
a distinguishable 2-FSK; above 100 kHz it is too wide (LoRa
territory). FSK at +/- 25 kHz produces ``chirp_span = 47 kHz``."""

_FSK_CHIRP_SPAN_MAX_HZ: float = 100_000.0

_FSK_CHIRP_SPAN_SHARPNESS: float = 2e-4
"""Sigmoid sharpness (1/Hz) for the FSK chirp-span gates. 2e-4 means a
transition width of ~10 kHz at the floor and at the cap, sharp enough
that the FSK fixture (chirp_span=47 kHz, in the middle of the band)
saturates both gates near 1."""

_FSK_RESIDUAL_FRACTION_MIN: float = 0.25
"""FSK chirp-fit residual fraction floor. A square-wave peak-bin
sequence (FSK) yields large residuals against a linear fit; the
WS-B-005 fixture has residual / span = 0.48. LoRa (clean chirp) has
0.31, so the gate at 0.25 admits FSK without confusing it for LoRa
when combined with the chirp_span cap above."""

_FSK_RESIDUAL_FRACTION_SHARPNESS: float = 30.0

# ---- Global signal-quality gate -------------------------------------------
#
# The four scoring formulae above each gate on modulation-specific features
# (peak_bin_std, hop_prominence, chirp_span). Each of those *can* fire on a
# pure-noise or low-SNR input -- noise produces a high peak_bin_std (peak bin
# jumps wildly), a fortuitous autocorrelation can have moderate prominence,
# and so on. The acceptance gate's honesty rule (Invariant B3 / WS-B-005
# criterion 5) requires SNR=-5 dB inputs to classify as "unknown" regardless
# of what feature accidentally matches. That is what this section gates.
#
# Empirical observation from the WS-B-005 probe:
#
#     flatness | PAPR_db | scenario
#     ---------+---------+--------------------------------
#       0.997  |  ~2     | any class at SNR=20 dB
#       0.974  |  ~5     | any class at SNR=10 dB
#       0.925  |  ~7     | any class at SNR=5 dB
#       0.851  |  ~9.5   | any class at SNR=-5 dB
#       0.843  |  ~10    | pure complex Gaussian noise
#
# The flatness drop from 0.925 (SNR=5) to 0.851 (SNR=-5) is the cliff -- a
# sigmoid centred on ~0.89 with sharpness 30 transitions cleanly across
# that interval and saturates near 1 at SNR>=5 dB.
_QUALITY_FLATNESS_KNEE: float = 0.87
"""Sigmoid knee for the signal-quality gate, measured on the envelope
flatness feature. Below this, every family score is multiplicatively
dampened. 0.87 sits between the SNR=5 dB case (flatness ~0.93) and the
SNR=-5 dB case (flatness ~0.85), so the 5 dB scenarios pass with mild
dampening (multiplier ~0.84) while -5 dB scenarios are heavily damped
(multiplier ~0.36)."""

_QUALITY_FLATNESS_SHARPNESS: float = 30.0
"""Sigmoid sharpness for the signal-quality gate. Steep enough that the
SNR=5 vs SNR=-5 cliff is fully resolved across the 0.93->0.85 flatness
band."""


def _signal_quality_multiplier(features: npt.NDArray[np.float64]) -> float:
    """Return a ``[0, 1]`` multiplier that drops to zero on noise-like input.

    Reads the envelope-flatness feature (index 1) and applies a sigmoid
    centred on the SNR=5 dB / SNR=-5 dB cliff. The multiplier is applied
    uniformly to every family score, so it cannot bias one class over
    another -- its only job is to send *all* scores below
    ``CONFIDENCE_FLOOR`` when the input is noise-dominated.

    Honesty contract: a synthetic test that lowers SNR until classify
    returns "unknown" should not see a class boundary -- it should see a
    smooth descent of *all* scores together. This multiplier provides
    that.
    """
    flatness = float(features[1])
    return _sigmoid(_QUALITY_FLATNESS_SHARPNESS * (flatness - _QUALITY_FLATNESS_KNEE))


# ---- LoRa thresholds -------------------------------------------------------
_LORA_RESIDUAL_FRACTION_MAX: float = 0.4
"""LoRa chirp-fit residual fraction ceiling. A clean linear chirp
yields small residuals; the WS-B-005 fixture has 0.31. The ceiling at
0.4 admits the fixture but excludes FSK's 0.48 piecewise-constant
residuals."""

_LORA_RESIDUAL_FRACTION_SHARPNESS: float = 50.0
"""Sharpness for the LoRa residual-fraction sigmoid. Steep so the
fixture's 0.31 (below the 0.4 max) saturates near 1, while FSK's 0.48
(above the max) drops below 0.05."""

_LORA_CHIRP_SPAN_MIN_HZ: float = 80_000.0
"""LoRa minimum chirp span. The SF7 / 125 kHz BW fixture produces
``chirp_span = 113 kHz`` (slightly less than full BW due to STFT
edge effects). 80 kHz admits the fixture without admitting FSK
(``chirp_span = 47 kHz``)."""

_LORA_CHIRP_SPAN_MAX_HZ: float = 600_000.0
"""LoRa maximum chirp span. Beyond ~600 kHz the signal is more
likely FHSS sweeping its hop set than a clean LoRa chirp -- the
WS-B-005 FHSS fixture has ``chirp_span = 500 kHz`` (the slope is
spurious, between argmax bins of distant hops)."""

_LORA_CHIRP_SPAN_SHARPNESS: float = 1e-4
"""Sigmoid sharpness (1/Hz) for the LoRa chirp-span gates. Sharp enough
that the SF7/125 kHz fixture (chirp_span=113 kHz, well above the
80 kHz floor) saturates the floor gate near 1; the cap at 600 kHz is
the FHSS-discriminator."""

_LORA_PEAK_BIN_STD_TARGET: float = 9.0
"""LoRa per-segment peak-bin std centred on 9 (fixture value), sigma=8.
Penalises FHSS-like (45) and noise-like (73) peak_bin_std but stays
wide enough that variants of the LoRa SF/BW combination (different
sweep rates land at slightly different peak_bin_std) still score
high."""

_LORA_PEAK_BIN_STD_SIGMA: float = 8.0


def _sigmoid(x: float) -> float:
    """Numerically stable scalar sigmoid; output bounded to ``[0, 1]``."""
    if x >= 0.0:
        z = float(np.exp(-x))
        return 1.0 / (1.0 + z)
    z = float(np.exp(x))
    return z / (1.0 + z)


def _gaussian(x: float, mean: float, sigma: float) -> float:
    """Un-normalised Gaussian; peak value 1.0 at ``x == mean``."""
    if sigma <= 0.0:
        return 0.0
    z = (x - mean) / sigma
    return float(np.exp(-0.5 * z * z))


def _score_cw(features: npt.NDArray[np.float64]) -> float:
    """CW score: one IF-histogram peak + constant per-segment peak bin."""
    if_peaks = float(features[4])
    peak_bin_std = float(features[15])

    peak_count_score = _gaussian(if_peaks, _CW_IF_PEAKS_TARGET, _CW_IF_PEAKS_SIGMA)
    # Smooth ceiling on peak_bin_std: 1 when peak_bin_std < 1.5, falling fast above.
    peak_bin_score = _sigmoid(_CW_PEAK_BIN_STD_SHARPNESS * (_CW_PEAK_BIN_STD_MAX - peak_bin_std))
    return float(peak_count_score * peak_bin_score)


def _score_fhss(features: npt.NDArray[np.float64]) -> float:
    """FHSS score: large-but-bounded peak_bin_std + hop autocorrelation.

    ``peak_bin_std`` is the load-bearing discriminator: the STFT
    per-segment argmax bin records every hop, and the std of that
    sequence cleanly separates FHSS (~45 for the 8-bin fixture across
    all SNRs) from FSK (~6), LoRa (~9), CW (~0), and noise (~73). The
    global ``signal_quality_multiplier`` handles the noise case
    indirectly via envelope flatness.

    ``if_peaks`` is *not* used here: SNR-driven histogram-bin merging
    can collapse the 8-bin FHSS fixture to as few as 2 visible peaks
    at SNR=10 dB, which would otherwise drop the FHSS score below the
    confidence floor. The peak-bin-std band-pass is sufficient against
    every other class.
    """
    hop_prominence = float(features[6])
    peak_bin_std = float(features[15])

    # Smooth bandpass on peak_bin_std: high between 20 and 60, low outside.
    floor_score = _sigmoid(_FHSS_PEAK_BIN_STD_SHARPNESS * (peak_bin_std - _FHSS_PEAK_BIN_STD_FLOOR))
    cap_score = _sigmoid(_FHSS_PEAK_BIN_STD_SHARPNESS * (_FHSS_PEAK_BIN_STD_CAP - peak_bin_std))
    peak_bin_score = floor_score * cap_score
    hop_score = _sigmoid(10.0 * (hop_prominence - _FHSS_HOP_PROMINENCE_FLOOR))
    return float(peak_bin_score * hop_score)


def _score_fsk(features: npt.NDArray[np.float64]) -> float:
    """FSK score: few IF peaks + small-but-non-zero chirp span + large residual fraction."""
    if_peaks = float(features[4])
    chirp_residual_rms = float(features[8])
    chirp_span = float(features[9])
    peak_bin_std = float(features[15])

    peak_count_score = _gaussian(if_peaks, _FSK_PEAK_COUNT_TARGET, _FSK_PEAK_COUNT_SIGMA)
    peak_bin_score = _gaussian(peak_bin_std, _FSK_PEAK_BIN_STD_TARGET, _FSK_PEAK_BIN_STD_SIGMA)

    # Chirp-span band-pass: in [_FSK_CHIRP_SPAN_MIN_HZ, _FSK_CHIRP_SPAN_MAX_HZ].
    span_floor_score = _sigmoid(_FSK_CHIRP_SPAN_SHARPNESS * (chirp_span - _FSK_CHIRP_SPAN_MIN_HZ))
    span_cap_score = _sigmoid(_FSK_CHIRP_SPAN_SHARPNESS * (_FSK_CHIRP_SPAN_MAX_HZ - chirp_span))
    span_score = span_floor_score * span_cap_score

    if chirp_span <= 0.0:
        residual_score = 0.0
    else:
        residual_fraction = chirp_residual_rms / chirp_span
        residual_score = _sigmoid(
            _FSK_RESIDUAL_FRACTION_SHARPNESS * (residual_fraction - _FSK_RESIDUAL_FRACTION_MIN)
        )
    return float(peak_count_score * peak_bin_score * span_score * residual_score)


def _score_lora(features: npt.NDArray[np.float64]) -> float:
    """LoRa score: wide chirp span + small residual fraction + LoRa-like peak_bin_std."""
    chirp_residual_rms = float(features[8])
    chirp_span = float(features[9])
    peak_bin_std = float(features[15])

    if chirp_span <= 0.0:
        return 0.0

    residual_fraction = chirp_residual_rms / chirp_span
    residual_score = _sigmoid(
        _LORA_RESIDUAL_FRACTION_SHARPNESS * (_LORA_RESIDUAL_FRACTION_MAX - residual_fraction)
    )
    span_floor_score = _sigmoid(_LORA_CHIRP_SPAN_SHARPNESS * (chirp_span - _LORA_CHIRP_SPAN_MIN_HZ))
    span_cap_score = _sigmoid(_LORA_CHIRP_SPAN_SHARPNESS * (_LORA_CHIRP_SPAN_MAX_HZ - chirp_span))
    span_score = span_floor_score * span_cap_score
    peak_bin_score = _gaussian(peak_bin_std, _LORA_PEAK_BIN_STD_TARGET, _LORA_PEAK_BIN_STD_SIGMA)
    return float(residual_score * span_score * peak_bin_score)


def score_families(features: npt.NDArray[np.float64], fs: float) -> FamilyScores:
    """Compute per-family scores from the feature vector.

    Args:
        features: ``FEATURE_VECTOR_LENGTH``-element ``float64`` vector
            from ``features.extract_features``.
        fs: Sample rate in Hz. Kept in the public signature for forward
            compatibility (future families may scale thresholds against
            ``fs``); the current rules read absolute-Hz thresholds and
            therefore do not consume ``fs`` directly.

    Returns:
        A ``FamilyScores`` with each entry in ``[0, 1]``.

    Raises:
        ValueError: if ``features`` is not the expected length or
            contains non-finite entries.
    """
    if features.ndim != 1:
        msg = f"features must be 1-D, got {features.ndim}-D"
        raise ValueError(msg)
    if not np.all(np.isfinite(features)):
        msg = "features contains non-finite entries"
        raise ValueError(msg)
    # fs is retained in the signature for forward compatibility; the
    # current scoring uses absolute Hz thresholds.
    del fs
    # Global SNR-quality multiplier: drops every family score uniformly
    # to zero on noise-dominated input. Honesty rule (Invariant B3): the
    # ``"unknown"`` short-circuit must fire on noise regardless of which
    # modulation-specific feature happens to fluctuate above its
    # threshold.
    quality = _signal_quality_multiplier(features)
    return FamilyScores(
        cw=quality * _score_cw(features),
        fhss=quality * _score_fhss(features),
        fsk=quality * _score_fsk(features),
        lora=quality * _score_lora(features),
    )


_LABELS: tuple[ModulationLabel, ...] = ("cw", "fhss", "fsk", "lora")


def classify_from_features(
    features: npt.NDArray[np.float64], *, fs: float
) -> tuple[ModulationLabel, float, FamilyScores]:
    """Map a feature vector to ``(label, confidence, scores)``.

    Args:
        features: The feature vector produced by
            ``features.extract_features``.
        fs: Sample rate in Hz. See ``score_families`` for the role of
            this parameter.

    Returns:
        ``(label, confidence, scores)``:

        * ``label`` -- one of ``{"cw", "fhss", "fsk", "lora", "unknown"}``.
        * ``confidence`` -- a probability-like scalar in ``[0, 1]``.
          For a confident classification, this is the margin-scaled
          max score (``max_score * (0.5 + 0.5 * margin)``, where
          ``margin`` is the gap to the second-best score). For the
          ``"unknown"`` branch, this is the un-scaled max score
          (always below ``CONFIDENCE_FLOOR``).
        * ``scores`` -- the per-family ``FamilyScores`` breakdown for
          diagnostics.

    The honesty rule: ``"unknown"`` is returned whenever the max family
    score falls below ``CONFIDENCE_FLOOR``. This is the analog of
    ``EmitterClass.UNKNOWN`` one layer up at the ``BearingReport``
    level (WS-B-006's mapping job); ``INTERFACES.md`` Section 1 spells
    out the distinction.
    """
    scores = score_families(features, fs)
    score_array = np.array([scores.cw, scores.fhss, scores.fsk, scores.lora], dtype=np.float64)
    argmax = int(np.argmax(score_array))
    max_score = float(score_array[argmax])

    if max_score < CONFIDENCE_FLOOR:
        return "unknown", max_score, scores

    sorted_scores = np.sort(score_array)[::-1]
    margin = float(sorted_scores[0] - sorted_scores[1])
    # Margin-aware confidence: when the winning family score is far
    # above the runner-up, confidence equals the raw max score. When
    # the second-best is close, the confidence is reduced by up to
    # 30% to reflect the ambiguity. The ``tanh(5 * margin)`` saturates
    # near 1.0 for margins >= 0.5, so a clean win (typical of the
    # WS-B-005 fixtures where the runner-up scores ~0.05) yields
    # ``confidence = max_score`` with no penalty.
    margin_factor = 0.7 + 0.3 * float(np.tanh(5.0 * margin))
    confidence = max_score * margin_factor
    confidence_clamped = float(np.clip(confidence, 0.0, 1.0))
    return _LABELS[argmax], confidence_clamped, scores
