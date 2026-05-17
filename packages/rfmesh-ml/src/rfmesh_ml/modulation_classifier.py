"""L3 modulation-class classifier (CW / FHSS / FSK / LoRa / UNKNOWN).

Public API surface of ``rfmesh-ml`` for the v1.0 BoTH3 demo. Pure-numpy
runtime (the ``backend="rules"`` path); an optional, training-only
PyTorch CNN backend exists as a skeleton (``backend="cnn"``) and is
*not* exercised at inference time in v1.0 -- the constructor lazily
imports torch *only* when explicitly requested, and the rules path
has zero torch dependency in its import graph.

Module shape
------------

* ``ClassificationResult`` -- frozen dataclass returned by
  ``classify``. Carries ``modulation_class`` (the string label),
  ``confidence`` (in ``[0, 1]``), and the raw ``feature_vector`` for
  diagnostics.
* ``FeatureExtractionError`` -- raised by feature extraction on
  degenerate IQ inputs (empty block, all-zero block where the
  envelope statistics are undefined). Subclasses ``ValueError`` so
  callers catching the generic ``ValueError`` continue to work.
* ``ModulationClassifier`` -- the public entry point.
  ``classify(iq, fs)`` returns a ``ClassificationResult``.

Honesty rule: ``"unknown"`` and the ``EmitterClass.UNKNOWN`` distinction
-----------------------------------------------------------------------

This module emits a *modulation-class* string ("cw" / "fhss" / "fsk" /
"lora" / "unknown"). It deliberately does *not* emit
``EmitterClass`` from ``rfmesh_contracts.enums`` -- the
modulation-class -> ``EmitterClass`` mapping is **WS-B-006**'s job
(the threat-library layer). For the WS-B-006 author reading this
docstring:

* The ``"unknown"`` *modulation_class* returned here is the
  classifier-ran-but-not-confident case. It must map to
  ``EmitterClass.UNKNOWN`` (not to ``None``) at the
  ``BearingReport.emitter_class`` boundary.
* The ``None`` *emitter_class* one layer up (the
  classifier-did-not-run case) corresponds to a node that has not
  built a ``ModulationClassifier`` at all (no L3 capability). It is
  not produced by this module; it is the absence of a call.

``INTERFACES.md`` Section 1 ``EmitterClass`` carries the full
semantics; this module's role is the modulation layer beneath it.

Backend choice
--------------

* ``backend="rules"`` (default) -- pure numpy, deterministic, demo
  path. The thresholds in ``rules.py`` are tuned against the
  WS-B-005 acceptance scenarios at SNR in {5, 10, 20} dB; the
  ``"unknown"`` honesty rule fires at SNR <= -5 dB.
* ``backend="cnn"`` -- training-and-ONNX-export skeleton for the
  v1.5+ classifier that trains against real captures. PyTorch is
  imported lazily inside the constructor *only* when this backend
  is selected; missing torch raises ``ImportError`` with a message
  naming torch as a v1.0 optional dependency. **The CNN backend's
  inference is not exercised by the WS-B-005 test suite** -- it is
  scaffolding for the next release, not a v1.0 demo path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from rfmesh_ml.features import FEATURE_VECTOR_LENGTH, extract_features
from rfmesh_ml.rules import (
    CONFIDENCE_FLOOR,
    FamilyScores,
    ModulationLabel,
    classify_from_features,
)

__all__ = [
    "CONFIDENCE_FLOOR",
    "FEATURE_VECTOR_LENGTH",
    "ClassificationResult",
    "FamilyScores",
    "FeatureExtractionError",
    "ModulationClassifier",
    "ModulationLabel",
]

Backend = Literal["rules", "cnn"]


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Output of ``ModulationClassifier.classify``.

    Attributes
    ----------
    modulation_class
        One of ``{"cw", "fhss", "fsk", "lora", "unknown"}``. The
        ``"unknown"`` case is meaningful: the classifier ran and is not
        confident enough to assign a label (Invariant B3,
        ``EmitterClass.UNKNOWN`` analog one layer up).
    confidence
        Probability-like scalar in ``[0.0, 1.0]``. For the rules
        backend, this is the margin-scaled max family score. See
        ``rules.py`` for the formula.
    feature_vector
        The raw feature vector ``features.extract_features`` produced
        for this IQ block. Length is ``FEATURE_VECTOR_LENGTH``, dtype
        ``float64``, all entries finite. Diagnostic only: downstream
        consumers (WS-B-006 threat mapping, ops dashboard's "why did
        the classifier say that?" panel) read ``modulation_class`` and
        ``confidence``; ``feature_vector`` is for debugging,
        threshold tuning, and offline analysis.
    """

    modulation_class: ModulationLabel
    confidence: float
    feature_vector: npt.NDArray[np.float64]


class FeatureExtractionError(ValueError):
    """Raised by feature extraction on a degenerate IQ input.

    Examples: empty IQ block, IQ block shorter than the STFT window,
    all-zero block where the envelope statistics are undefined.

    Subclasses ``ValueError`` so existing ``except ValueError`` callers
    catch it (Invariant B3 -- fail loudly, but compose with the
    standard exception hierarchy). Distinct from ``TypeError`` for
    dtype mismatches, which the underlying extractor raises directly.
    """


class ModulationClassifier:
    """Pure-numpy modulation-class classifier (v1.0).

    The CNN backend (``backend="cnn"``) is a training-and-export
    skeleton; v1.0's runtime path is ``backend="rules"`` and is the
    default. See module docstring for the rules-vs-CNN distinction and
    the ``"unknown"`` honesty rule.

    Parameters
    ----------
    backend
        ``"rules"`` (default, pure numpy) or ``"cnn"`` (lazily imports
        PyTorch; raises ``ImportError`` if torch is not installed).
        v1.0 ships ``"rules"`` as the demo path.
    """

    def __init__(self, *, backend: Backend = "rules") -> None:
        if backend not in ("rules", "cnn"):
            msg = f"backend must be 'rules' or 'cnn' (got {backend!r})"
            raise ValueError(msg)
        self._backend: Backend = backend
        # Lazy torch import: only resolved when backend="cnn" is selected.
        # The rules path must keep torch out of the import graph entirely
        # so the v1.0 deploy footprint (RPi class) does not need a torch
        # wheel.
        self._cnn: object | None = None
        if backend == "cnn":
            self._cnn = _build_cnn_backend_or_raise()

    @property
    def backend(self) -> Backend:
        """Which backend this classifier is running -- ``"rules"`` or ``"cnn"``."""
        return self._backend

    def classify(
        self,
        iq: npt.NDArray[np.complex64],
        *,
        fs: float,
    ) -> ClassificationResult:
        """Classify a complex64 IQ block.

        Args:
            iq: ``complex64`` IQ samples. Strict on dtype; passing
                ``complex128`` (or any other dtype) raises ``TypeError``
                with a message naming ``complex64``. The same strictness
                ``rfmesh-dsp`` applies at its API boundaries.
            fs: Sample rate in Hz. Must be > 0.

        Returns:
            A ``ClassificationResult`` carrying the modulation-class
            label, the confidence in ``[0, 1]``, and the raw feature
            vector.

        Raises:
            TypeError: if ``iq.dtype != complex64``.
            ValueError: if ``iq`` is empty, ``fs <= 0``, or feature
                extraction produces a degenerate result.
            NotImplementedError: if ``backend == "cnn"`` and the
                inference path is invoked (v1.0 ships the CNN backend
                as a training-only skeleton; inference is reserved for
                v1.5+ once training has happened).
        """
        try:
            feature_vector = extract_features(iq, fs=fs)
        except ValueError as exc:
            # Re-wrap into FeatureExtractionError so consumers that
            # specifically want to distinguish "feature extraction
            # failed" from "classification produced unknown" can. The
            # underlying ValueError is preserved as __cause__.
            raise FeatureExtractionError(str(exc)) from exc

        if self._backend == "cnn":
            # v1.0: the CNN backend ships as training-and-export
            # scaffolding only. Inference would require trained weights
            # we deliberately do not bundle yet (no real-capture data
            # has been gathered; bundling synthetic-only weights would
            # be the same kind of "magic-constant honesty leak"
            # Invariant B3 forbids).
            msg = (
                "backend='cnn' is a training-and-export skeleton in v1.0; "
                "inference is rules-based only. Use backend='rules' for "
                "the demo path."
            )
            raise NotImplementedError(msg)

        label, confidence, _scores = classify_from_features(feature_vector, fs=fs)
        return ClassificationResult(
            modulation_class=label,
            confidence=confidence,
            feature_vector=feature_vector,
        )


def _build_cnn_backend_or_raise() -> object:
    """Lazily build the CNN backend; raise ``ImportError`` if torch absent.

    Kept as a module-level helper (rather than inlined into ``__init__``)
    so the ``backend="rules"`` path provably never reaches this code. A
    static-analysis check (lint-imports plus the test
    ``test_rules_backend_does_not_import_torch``) confirms torch never
    appears at module top-level.
    """
    try:
        # PyTorch is an OPTIONAL v1.0 dependency. Importing it inside this
        # function (rather than at module top-level) keeps the rules-only
        # demo path free of torch. The ``type: ignore`` is required because
        # torch is not part of the workspace dependency set; the mypy
        # workspace-wide ``warn_unused_ignores`` setting is satisfied even
        # when torch happens to be installed since the import is still
        # statically un-resolvable from rfmesh-ml's declared deps.
        # Import deliberately inside the function so the rules-only demo
        # path never resolves the torch module. Catching ImportError below
        # is the contract; a top-level import would defeat the optional-
        # dependency design.
        import torch  # type: ignore[import-not-found, unused-ignore]  # noqa: F401, PLC0415
    except ImportError as exc:
        msg = (
            "backend='cnn' requires PyTorch, which is an OPTIONAL v1.0 "
            "dependency of rfmesh-ml. Install torch (a /uvadd-request "
            "would be the formal channel) or use backend='rules' for the "
            "rules-based v1.0 demo path."
        )
        raise ImportError(msg) from exc

    # The actual SmallCNN class plus its train/export_onnx scaffolding
    # is intentionally absent from v1.0 -- it would be untested code on
    # a critical path (Invariant B3 / pitch slide #5: open extensible,
    # not opaque). When the CNN backend graduates, the class lands in a
    # new module (rfmesh_ml.cnn_backend) and is wired here. A sentinel
    # object stands in for the missing implementation so the constructor
    # can succeed when torch IS installed (the test path
    # test_cnn_backend_raises_without_torch covers the negative branch).
    return _CNNBackendStub()


class _CNNBackendStub:
    """Placeholder for the v1.5+ CNN backend.

    Constructible when torch is installed; calling ``classify`` on a
    classifier built with ``backend="cnn"`` raises
    ``NotImplementedError`` (handled in ``ModulationClassifier.classify``).
    This shape lets the lazy-import contract be tested without
    smuggling a real CNN into v1.0.
    """
