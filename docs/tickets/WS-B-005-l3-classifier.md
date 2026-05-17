# TICKET WS-B-005: L3 modulation-class classifier (CW / FHSS / FSK / LoRa / UNKNOWN)

## Goal (one sentence)

Implement `packages/rfmesh-ml/src/rfmesh_ml/modulation_classifier.py` —
a pure-numpy `ModulationClassifier` that takes a `complex64` IQ block
and returns a `ClassificationResult(modulation_class, confidence,
feature_vector)`, classifying among `{"cw", "fhss", "fsk", "lora",
"unknown"}` via a deterministic feature extractor + rules-based core
(with an optional, behind-a-parameter PyTorch CNN backend stubbed for
training-pipeline + ONNX export — runtime stays pure-numpy in v1.0).

## Context (links only, not content)

- Contracts touched (read-only):
  `rfmesh_contracts.enums.EmitterClass` (semantic layer this module
  *feeds into* — but does NOT itself emit; `EmitterClass`-level
  classification is WS-B-006's job, which wraps this module's
  modulation-class output in a threat-profile mapping).
  `rfmesh_contracts.messages.BearingReport.emitter_class` /
  `classification_confidence` (the eventual sink; see `INTERFACES.md`
  §3 for the `None` vs `UNKNOWN` distinction this ticket preserves).
- `INTERFACES.md` §1 `EmitterClass` enum semantics — particularly
  the `UNKNOWN` member's load-bearing role and the "stub at v1.0.0"
  status of `POLE21` / `VOLNOREZ` / `DRONEID`.
- Architecture: `HANDOFF_TO_CLAUDE_CODE_LEAD.md` §0 Advantage #5 —
  *"Open, extensible threat library as moat."* This ticket delivers
  the **modulation-class** layer of that pitch; WS-B-006 delivers
  the `EmitterClass` mapping on top (e.g. `cw` + 868 MHz LoRa profile
  = ELRS LoRa-FHSS hint). v1.0 visible artefact: a deterministic
  classifier the demo can run on synthetic IQ with honest "unknown"
  output at low SNR.
- `HANDOFF_TO_CLAUDE_CODE_LEAD.md` §5 "Workstream B remaining" — WS-B-005
  and WS-B-006 are the last two B tickets before B stands down.
- `WORKSTREAMS.md` §1 Workstream B row (owns `packages/rfmesh-ml/`)
  and §2 Workstream B deliverables item 5 (L3 pipeline — STFT features,
  small CNN/ResNet baseline, ONNX export, one module per `EmitterClass`
  in `threats/` — the open-structure moat).
- DSP front-end (read-only): `rfmesh_dsp.spectrum.compute_spectrogram`
  (the salvaged STFT — `(times, freqs, sxx_db)`, scaling="density",
  `mode="psd"`, fftshifted). Use as the canonical STFT entry point; do
  not re-implement.
- AGENTS.md invariants: B2 (honest sigma — *not* applicable to this
  module since no `BearingReport` is emitted; the analog is the
  honest "unknown" output at low SNR, see acceptance criterion 5);
  B3 (no silent fallbacks — low-confidence outputs return
  `"unknown"`, never a fabricated label); B5 (pure: no network, no
  file I/O at runtime beyond loading bundled model weights at import
  if the CNN backend is wired up).
- Prior tickets this depends on: none in `rfmesh-ml` (this is the
  first ML ticket). Reuses WS-B-002's nothing-yet (this module is
  independent of array DSP), reuses the salvaged `dsp.spectrum`.
- Salvage: none new in this ticket. `dsp.spectrum` was already
  salvaged TAKE-grade in `SALVAGE_AUDIT.md` Part 3.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-ml -v` passes. New tests added to
   `packages/rfmesh-ml/tests/test_modulation_classifier.py`:

   - `test_classification_result_shape` —
     `ClassificationResult` is a frozen dataclass (or equivalent
     immutable container, e.g. `dataclass(frozen=True, slots=True)`)
     exposing exactly three fields in this order:
       * `modulation_class: Literal["cw", "fhss", "fsk", "lora",
         "unknown"]` (the `str` value matches the literal exactly —
         lowercase, no leading/trailing whitespace);
       * `confidence: float` constrained to `[0.0, 1.0]`;
       * `feature_vector: numpy.typing.NDArray[numpy.float64]` of
         a fixed length (one-dimensional, length pinned by
         `features.py`'s `FEATURE_VECTOR_LENGTH` constant — see
         implementation notes).
   - `test_classify_cw_high_snr` — parametrised over SNR ∈
     `{5, 10, 20}` dB and over `backend ∈ {"rules"}` (CNN backend
     not exercised in v1.0 test gate; see criterion 4): generate a
     single complex tone at known frequency offset, classify, assert
     `modulation_class == "cw"` and `confidence >= 0.7`. Seeded
     numpy RNG (seed = 42) so the test is deterministic across runs.
   - `test_classify_fhss_high_snr` — same SNR sweep: synthesise a
     frequency-hopping signal (e.g. 50 hops/s over 8 bins spanning
     the analysis bandwidth, dwell time configurable, hop pattern
     random under a seeded RNG), classify, assert `"fhss"` and
     `confidence >= 0.7`. The hop-rate detector in `features.py`
     drives this — see implementation notes.
   - `test_classify_fsk_high_snr` — same SNR sweep: synthesise a
     2-FSK signal with known deviation (e.g. ±25 kHz around a
     centre tone, symbol rate 9.6 kHz — narrow constant FM
     deviation as opposed to FHSS's wide jumps). Assert `"fsk"`
     and `confidence >= 0.7`.
   - `test_classify_lora_high_snr` — same SNR sweep: synthesise an
     up-chirp + down-chirp LoRa-like waveform (e.g. spreading factor
     7, 125 kHz bandwidth, the canonical SX1276 chirp parameters
     from `lora_beacon_spec.md`). Assert `"lora"` and
     `confidence >= 0.7`.
   - `test_classify_unknown_low_snr` — parametrised over the four
     classes above at SNR = -5 dB: classify, assert
     `modulation_class == "unknown"` (the deterministic threshold
     in `rules.py` short-circuits to `"unknown"` when no feature
     family scores above its empirical floor; see implementation
     notes for the threshold).
   - `test_classify_unknown_pure_noise` — pure complex Gaussian
     noise input, no signal: `modulation_class == "unknown"` and
     `confidence < 0.5`. The classifier does not fabricate a label
     on noise alone (Invariant B3).
   - `test_feature_vector_is_finite` — `feature_vector` contains
     no `NaN`, no `inf`, no `-inf` across all six scenarios above.
     Numerical hygiene gate — features computed on edge inputs
     (e.g. all-zero IQ block, single-sample block) should either
     raise `FeatureExtractionError` or produce finite values; this
     test pins the no-NaN side of the contract.
   - `test_iq_dtype_complex64_strict` — calling
     `ModulationClassifier.classify(iq=np.array([...], dtype=
     np.complex128))` raises `TypeError` with a message naming
     `complex64`. The module is strict about dtype on input; the
     existing `rfmesh-dsp` modules are strict the same way.
   - `test_iq_empty_raises` — empty IQ block raises `ValueError`
     with a non-empty message. Same shape as `dsp.spectrum`'s edge
     handling.

   Total: 4 classes × 3 SNRs = 12 high-confidence positive tests,
   plus 4 low-SNR `"unknown"` tests, plus 1 pure-noise test, plus 4
   structural/hygiene tests = **21 tests**.

2. Feature extractor (`features.py`) is pure numpy + uses
   `rfmesh_dsp.spectrum.compute_spectrogram` as its STFT entry point.
   Computes:
     * STFT magnitude (via `compute_spectrogram`, returning the
       `sxx_db` panel) — the time-frequency view every downstream
       feature reads from;
     * statistical features over `|iq|` — kurtosis (high for CW
       tones), envelope flatness, peak-to-average power ratio;
     * instantaneous frequency deviation histogram — narrow histogram
       = FSK / CW, wide bi-modal = FHSS, monotonically-sweeping = LoRa
       chirp signature;
     * hopping-rate detector — autocorrelation of per-segment peak-
       frequency bin over time, peak in the 10–10000 hops/s range
       triggers the FHSS feature;
     * chirp-rate detector — linear fit of instantaneous frequency vs
       time, residual-RMS small + non-zero slope = LoRa hint.
   All exposed as `extract_features(iq, fs) -> NDArray[float64]` with
   length `FEATURE_VECTOR_LENGTH` (the rules-based core consumes this
   vector; the CNN backend, when present, may consume the STFT
   directly instead — that is a backend-implementation choice and
   does NOT change the public `ClassificationResult.feature_vector`
   shape).

3. Rules-based classifier core (`rules.py`) is pure-numpy. Maps a
   feature vector to a `(class_label, confidence)` tuple via
   thresholded scoring per modulation family:
     * CW score: high kurtosis of `|iq|` + narrow instantaneous-
       frequency histogram → score in `[0, 1]`.
     * FHSS score: hopping-rate detector peak prominence + wide
       per-segment frequency variance.
     * FSK score: narrow constant deviation + low chirp slope.
     * LoRa score: high chirp-rate detector residual fit quality +
       non-zero slope + wide instantaneous bandwidth.
   The final label is the argmax of the four scores; the confidence
   is the *margin*-scaled max (margin = top score minus second-best,
   normalised). If `max_score < CONFIDENCE_FLOOR` (initial value
   `0.5`, tuned empirically against the test scenarios; record the
   chosen value in the docstring), label is `"unknown"` and
   confidence is the un-scaled max score. The floor is a module-
   level constant, not a magic number scattered in the code.

4. Optional CNN backend (training-only in v1.0). `modulation_
   classifier.py` accepts `backend: Literal["rules", "cnn"] = "rules"`
   on construction:
     * `"rules"` is the v1.0 runtime path and is the default. **Pure
       numpy. No PyTorch import at module top-level on the rules
       path.**
     * `"cnn"` lazily imports PyTorch inside the constructor *only
       when selected*. If PyTorch is not installed, the constructor
       raises `ImportError` with a message naming `torch` as a
       v1.0 optional dependency (does NOT install via this ticket
       — `/uvadd-request` would be a separate proposal). The CNN
       backend is a stub in v1.0: a `SmallCNN` class (1-D CNN over
       the STFT panel, three conv blocks + linear head, output
       five-class softmax), a `train(...)` method that runs against
       synthetic IQ from `rfmesh-sdr.simulator` (in a dedicated
       script under `packages/rfmesh-ml/scripts/train_cnn.py` —
       *not* part of this ticket's acceptance), and an
       `export_onnx(path)` method skeleton. **The CNN backend is
       NOT exercised by this ticket's tests.** Test only the
       *path* — `test_cnn_backend_raises_without_torch` constructs
       `ModulationClassifier(backend="cnn")` in an environment
       where torch is absent (skip with `pytest.importorskip`
       guarding the *negative* path: skip the test when torch IS
       installed; assert `ImportError` otherwise). Document
       limitation in the module docstring: "v1.0 ships the rules
       backend as the demo path; the CNN backend is a training-
       and-ONNX-export skeleton, not a runtime classifier."

5. **Honesty rule for the `"unknown"` output.** The classifier
   returns `"unknown"` whenever `max_score < CONFIDENCE_FLOOR` (see
   criterion 3). This mirrors `INTERFACES.md` §1's `EmitterClass.
   UNKNOWN` ("the classifier ran and is genuinely not confident
   enough to assign a label"). It is **distinct** from a `None`
   classification (which lives one layer up at the `BearingReport`
   level — WS-B-006's job is to honour that distinction when it
   maps `ClassificationResult` to `EmitterClass`). Document the
   distinction in `modulation_classifier.py`'s module docstring
   explicitly so the WS-B-006 author reads it before mapping.

6. Synthetic-IQ test fixtures live in
   `packages/rfmesh-ml/tests/conftest.py`. The fixtures are
   self-contained: they generate IQ directly with numpy primitives
   (sinusoids, FM modulation, frequency-hopping switches, linear
   chirps). They **do NOT import from `rfmesh-sdr.simulator`** —
   that is a sibling-workstream dependency we deliberately do not
   pull in here. The CNN-backend training script (out of scope for
   this ticket; lives under `scripts/`) is the place that wires up
   `rfmesh-sdr.simulator`-driven training data. Rationale: the
   classifier unit tests must be readable and reproducible from
   the IQ generation up; pulling in the simulator obscures what
   IQ the test classifier sees.

7. `uv run mypy packages/rfmesh-ml` clean (strict mode, the
   workspace default).

8. `uv run ruff check packages/rfmesh-ml` clean.

9. Imports across the new source files are limited to:
     * `numpy`, `numpy.typing`
     * stdlib (`math`, `dataclasses`, `typing`, `collections.abc`,
       `logging`, `pathlib` — `pathlib` only for the CNN backend's
       `export_onnx`)
     * `rfmesh_dsp.spectrum` (read-only consumer for STFT)
     * `rfmesh_contracts.enums` — **only if the module needs to
       reference `EmitterClass` for docstring or type-narrowing**;
       this module emits the modulation-class string layer, not
       `EmitterClass`, so the import is likely unnecessary; if
       present, it is read-only.
     * `torch` — **only inside the CNN backend's constructor**,
       behind a lazy import guarded by `backend == "cnn"`. Never
       at module top-level.
   **No imports from any sibling workstream package** (no
   `rfmesh_sdr.*`, no `rfmesh_node.*`, no `rfmesh_fusion.*`).
   `lint-imports` clean — the boundary is the
   `rfmesh-contracts` + `rfmesh-dsp` star, identical to every
   other workstream B module.

10. `rfmesh-ml` package metadata stays pure (Invariant B5): no
    network at import or at runtime; no subprocess; the only file
    I/O is *loading* model weights (CNN backend's `export_onnx` is
    the inverse — and is out-of-band, called from the training
    script, not from `classify()`).

## Out of scope (explicit non-goals)

- **The `EmitterClass`-level threat mapping.** Mapping
  `(modulation_class, frequency_band, hop_pattern, ...)` →
  `EmitterClass.ELRS` / `CROSSFIRE` / `GSM_JAMMER` / `POLE21` /
  `VOLNOREZ` / `DRONEID` is **WS-B-006**'s scope. WS-B-005 stops at
  the modulation layer.
- **Real-IQ training data.** v1.0 trains and tests on synthetic IQ
  only. Real-capture training (real ELRS modules, real DJI OcuSync,
  real Pole-21 captures) is a follow-up. The threat library's
  `POLE21` / `VOLNOREZ` / `DRONEID` profiles are documented stubs
  at v1.0 per `INTERFACES.md` §1 — this ticket does not unstub them.
- **DroneID / Pole-21 / Volnorez specific waveforms** in the
  classifier itself. Those are emitter-specific recognisers that
  belong in the threat library (WS-B-006) and require real-capture
  training before they can claim a probability honestly.
- **Hardware-side RPi inference deployment.** Onnxruntime install,
  RPi-specific optimisation, edge-deployment Docker images — not in
  this ticket. The CNN backend's `export_onnx` skeleton produces a
  file; making that file run on a Pi is the node-runtime workstream's
  concern.
- **The training pipeline itself** (data loaders, training loop,
  hyperparameter search, validation curves). The CNN backend ships
  with a *stub* `train(...)` method and an external `scripts/
  train_cnn.py` driver that may be empty in v1.0. Full training is
  out of scope; the architecture for it is what this ticket
  delivers.
- **Modifying `dsp.spectrum`** or any other DSP module. Consume the
  salvaged STFT verbatim; if it proves insufficient, write a
  SCRATCHPAD note rather than editing `rfmesh-dsp`.
- **Modifying `rfmesh-contracts`** (Invariant B1).
- **Adding a new runtime dependency.** Pure numpy + the existing
  `rfmesh-dsp` (which already pulls scipy via `dsp.spectrum`).
  PyTorch is a v1.0 *optional* dependency: not added by this
  ticket. A separate `/uvadd-request` would propose it if the CNN
  backend graduates from "stub" to "shipped".

## Files you may touch

- `packages/rfmesh-ml/src/rfmesh_ml/modulation_classifier.py`
  (create) — `ModulationClassifier`, `ClassificationResult`,
  `FeatureExtractionError`. The public API surface.
- `packages/rfmesh-ml/src/rfmesh_ml/features.py` (create) —
  `extract_features(iq, fs) -> NDArray[float64]` plus the
  `FEATURE_VECTOR_LENGTH` constant. Pure numpy, calls into
  `rfmesh_dsp.spectrum.compute_spectrogram`.
- `packages/rfmesh-ml/src/rfmesh_ml/rules.py` (create) —
  `classify_from_features(features) -> ClassificationResult`,
  the four-family scoring + argmax + confidence-floor logic.
  The `CONFIDENCE_FLOOR` constant lives here.
- `packages/rfmesh-ml/src/rfmesh_ml/__init__.py` (modify) —
  extend the existing module docstring with the v1.0 surface;
  re-export `ModulationClassifier`, `ClassificationResult`,
  `FeatureExtractionError`. Do NOT pre-export anything from a
  future WS-B-006.
- `packages/rfmesh-ml/tests/test_modulation_classifier.py`
  (create) — the 21 tests enumerated in acceptance criterion 1.
- `packages/rfmesh-ml/tests/conftest.py` (create) — synthetic IQ
  fixtures (CW, FHSS, FSK, LoRa, low-SNR variants, pure noise).
  Self-contained: numpy primitives only, no `rfmesh-sdr` import.
- `packages/rfmesh-ml/pyproject.toml` (extend only if needed for a
  pytest marker registration, e.g. `slow` if any test is
  long-running; do NOT add `[project] dependencies` here).

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant B1).
- `packages/rfmesh-dsp/**` (read-only consumer; if the STFT entry
  point proves insufficient, STOP and write a scratchpad note —
  do NOT modify `dsp.spectrum`).
- `packages/rfmesh-sdr/**` (read-only consumer at most; this
  ticket does NOT import from it — see acceptance criterion 6).
- Any sibling workstream package (`rfmesh-fusion`, `rfmesh-cot`,
  `rfmesh-node`, `rfmesh-ops`).
- `packages/rfmesh-ml/threats/` — this directory belongs to
  WS-B-006 (the `EmitterClass`-mapping layer). Even if it does
  not yet exist on disk, this ticket does NOT create modules
  under it. WS-B-006 is the one that authors the per-emitter
  YAML / Python profiles.

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste into the conversation the full output of:
    * `uv run pytest packages/rfmesh-ml -v`
    * `uv run mypy packages/rfmesh-ml`
    * `uv run ruff check packages/rfmesh-ml`
    * `uv run lint-imports` (if available in this workspace; if
      not, note that explicitly).
- If any of the 12 high-SNR positive tests *fail* (`modulation_
  class` mismatches or `confidence < 0.7`), STOP. Do **not**
  retune the `CONFIDENCE_FLOOR` or the per-family scoring
  weights downward to pass; the SNR=5 dB ≥ 0.7 confidence
  budget is the **demo slide number** ("our classifier
  achieves > 70% confidence above 5 dB SNR"). If the rules-based
  classifier cannot meet that honestly, the slide budget needs
  revision — write a scratchpad note under
  `.claude/scratchpad/ws-b-005-<date>.md` describing what
  confidence was achievable and on what scenario, and stop.
- If any of the 4 low-SNR `"unknown"` tests *fail* (the
  classifier produces a confident wrong label at SNR = -5 dB),
  STOP. The honesty rule (criterion 5, Invariant B3) is more
  important than coverage at high SNR — a classifier that
  *lies* at low SNR poisons fusion downstream by inducing
  spurious `EmitterClass` labels on bearings. Scratchpad and
  stop.
- If the optional CNN backend's lazy-import path fails its
  shape test (i.e. `backend="cnn"` *does* import torch at
  module top-level, or `backend="rules"` *does* import torch),
  STOP. The lazy-import contract is structural: the rules
  path must run with zero torch dependency in the import
  graph.
- If the feature extractor produces a `NaN` or `inf` on any
  test scenario, STOP. Numerical hygiene is non-negotiable;
  this is the kind of bug that silently produces a confident
  wrong answer in production (e.g. an `inf` in kurtosis caused
  by an all-zero block trips the CW threshold). Fix the
  extractor, do not the test.
- If you find that `rfmesh-dsp.spectrum.compute_spectrogram`
  is *missing* a feature this module needs (e.g. you need an
  STFT mode it does not expose, or you need a window function
  it does not document), STOP and write a SCRATCHPAD note.
  Do NOT modify `rfmesh-dsp` from this ticket. The
  Architect / Reviewer council decides whether to extend
  `rfmesh-dsp` or to compute the missing primitive locally.
- If you find that `BearingReport.emitter_class` /
  `classification_confidence` need a richer contract shape to
  carry the modulation-class layer cleanly (e.g. a structured
  `modulation_label` field alongside `emitter_class`), STOP
  and write `docs/adr/ADR-NNN-modulation-class-on-bearing-
  report.md` PROPOSED. Do not modify the contract.

## Implementation notes (non-binding, for guidance)

### Feature vector layout

A suggested `FEATURE_VECTOR_LENGTH = 16` (placeholder; the builder
chooses the exact length and records it in `features.py`'s module
docstring), laid out as:

  * `[0]` kurtosis of `|iq|`
  * `[1]` envelope flatness (geometric mean / arithmetic mean of
    `|iq|`)
  * `[2]` peak-to-average power ratio of `|iq|`
  * `[3]` instantaneous-frequency histogram entropy (low for CW /
    FSK, high for FHSS, low-but-shifted for LoRa)
  * `[4]` instantaneous-frequency histogram peak count (CW = 1,
    2-FSK = 2, FHSS = many, LoRa = continuous → 0 detected
    peaks)
  * `[5]` hopping-rate detector: peak of autocorrelation of
    per-segment STFT peak-bin sequence (units: hops per second)
  * `[6]` hopping-rate detector prominence (how peaked the
    autocorrelation peak is — distinguishes a real hop pattern
    from a noisy guess)
  * `[7]` chirp-rate detector: linear-fit slope of instantaneous
    frequency vs time
  * `[8]` chirp-rate detector: linear-fit residual RMS
  * `[9]` chirp-rate detector: instantaneous bandwidth swept
  * `[10..15]` reserved for the CNN backend's auxiliary STFT-derived
    summary statistics (mean, variance, skewness, kurtosis of the
    `sxx_db` panel along time and along frequency axes).

The exact length and contents are an implementation choice; the
binding constraint is that **`feature_vector` is a fixed-length
1-D `float64` numpy array, all entries finite, with the layout
documented in `features.py`'s module docstring**.

### Rules scoring

Each family score is in `[0, 1]`. A simple, defensible scheme:

  ```python
  # Pseudocode, not binding on names.
  cw_score   = sigmoid(features.kurtosis - CW_KURTOSIS_THRESHOLD) \
               * (1 - sigmoid(features.if_hist_entropy - LOW_ENTROPY))
  fhss_score = sigmoid(features.hop_rate_prominence
               - FHSS_HOP_PROMINENCE_THRESHOLD)
  fsk_score  = sigmoid(LOW_ENTROPY - features.if_hist_entropy) \
               * (1 - sigmoid(features.chirp_slope_abs
               - FSK_CHIRP_FLOOR))
  lora_score = sigmoid(features.chirp_residual_rms_inv
               - LORA_RESIDUAL_THRESHOLD) \
               * sigmoid(features.chirp_slope_abs
               - LORA_SLOPE_THRESHOLD)

  scores = numpy.array([cw_score, fhss_score, fsk_score, lora_score])
  argmax = numpy.argmax(scores)
  max_score = scores[argmax]
  if max_score < CONFIDENCE_FLOOR:
      return ClassificationResult("unknown", float(max_score), feats)
  # Margin-scaled confidence
  sorted_scores = numpy.sort(scores)[::-1]
  margin = sorted_scores[0] - sorted_scores[1]
  confidence = max(0.0, min(1.0, max_score * (0.5 + 0.5 * margin)))
  return ClassificationResult(LABELS[argmax], float(confidence), feats)
  ```

The thresholds (`CW_KURTOSIS_THRESHOLD`, `LOW_ENTROPY`,
`FHSS_HOP_PROMINENCE_THRESHOLD`, `FSK_CHIRP_FLOOR`,
`LORA_RESIDUAL_THRESHOLD`, `LORA_SLOPE_THRESHOLD`,
`CONFIDENCE_FLOOR`) are module-level constants in `rules.py`,
empirically tuned so that the test scenarios in acceptance
criterion 1 pass. **Record each chosen value in the module
docstring with a one-line "tuned for the WS-B-005 test
scenarios at SNR ≥ 5 dB" note.** The thresholds are the
honest analog of the WS-B sigma-honesty story: future
modulation families added by WS-B-006 may push these values,
and the recorded provenance keeps the choice auditable.

### Public API surface

```python
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt


ModulationLabel = Literal["cw", "fhss", "fsk", "lora", "unknown"]


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Output of ``ModulationClassifier.classify``.

    Attributes
    ----------
    modulation_class
        One of ``{"cw", "fhss", "fsk", "lora", "unknown"}``. The
        ``"unknown"`` case is meaningful: the classifier ran and
        is not confident enough to assign a label (Invariant B3,
        ``EmitterClass.UNKNOWN`` analog).
    confidence
        Probability-like scalar in ``[0.0, 1.0]``. For the rules
        backend, this is the margin-scaled max family score.
        See ``rules.py`` for the formula.
    feature_vector
        The raw feature vector ``features.py`` produced for this
        IQ block. Length is ``features.FEATURE_VECTOR_LENGTH``,
        dtype ``float64``, all entries finite. Diagnostic only:
        downstream consumers (WS-B-006) read ``modulation_class``
        and ``confidence``; ``feature_vector`` is for debugging,
        threshold tuning, and the ops dashboard's "why did the
        classifier say that?" panel.
    """

    modulation_class: ModulationLabel
    confidence: float
    feature_vector: npt.NDArray[np.float64]


class FeatureExtractionError(ValueError):
    """Raised by ``features.extract_features`` on a degenerate IQ
    input (e.g. empty block, all-zero block where statistics are
    undefined). Distinct from ``TypeError`` for dtype mismatches.
    Subclasses ``ValueError`` so existing ``except ValueError``
    callers catch it (Invariant B3 — fail loudly, but compose with
    standard exception hierarchies)."""


class ModulationClassifier:
    """Pure-numpy modulation-class classifier (v1.0).

    The CNN backend (``backend="cnn"``) is a training-and-export
    skeleton; v1.0's runtime path is ``backend="rules"`` and is
    the default. See module docstring for the rules-vs-CNN
    distinction and the ``"unknown"`` honesty rule.

    Parameters
    ----------
    backend
        ``"rules"`` (default, pure numpy) or ``"cnn"`` (lazily
        imports PyTorch; raises ``ImportError`` if torch is not
        installed). v1.0 ships ``"rules"`` as the demo path.
    """

    def __init__(self, *, backend: Literal["rules", "cnn"] = "rules") -> None: ...

    def classify(
        self,
        iq: npt.NDArray[np.complex64],
        *,
        fs: float,
    ) -> ClassificationResult: ...
```

### Why this exists

The pitch slide on Advantage #5 (`HANDOFF` §0) needs a v1.0
deliverable that **demonstrates classification capability on
stage, deterministically, with no hardware**. The modulation-
class layer (CW / FHSS / FSK / LoRa) is the credible v1.0:

  * deterministic (rules-based core, seeded synthetic IQ tests);
  * hardware-free (the demo runs against `apps/demo-replay`
    recorded IQ or against `rfmesh-sdr.simulator` output —
    neither requires a Pi or a torch wheel);
  * honest (`"unknown"` at low SNR, never a fabricated label —
    the same Invariant B3 discipline as the rest of the
    project);
  * extensible (WS-B-006 wraps this output in the
    `EmitterClass` semantic layer; future modulation families
    are added by extending `rules.py` and the test grid, not by
    rewriting).

PyTorch is reserved for the v1.5+ CNN classifier that trains
against real captures; v1.0 ships the structure, not the
weights. That structure is the moat — RfPatrol Mk2 ships a
closed library at €5k+; Bukovel-AD's library is classified;
ours is one Python module per modulation family that an
operator can read.
