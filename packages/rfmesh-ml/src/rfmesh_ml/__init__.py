"""rfmesh-ml -- L3 edge ML emitter classification.

Workstream B. STFT-feature extraction, rules-based modulation
classifier (v1.0), and the future open threat-profile library
(``threats/``, WS-B-006 territory; not exported from this module).
Pure: no network I/O, no SDR access (Invariant B5).

v1.0 public API
---------------

* ``ModulationClassifier`` -- the entry point. ``classify(iq, fs)``
  returns a ``ClassificationResult``.
* ``ClassificationResult`` -- frozen dataclass with
  ``(modulation_class, confidence, feature_vector)``.
* ``FeatureExtractionError`` -- raised on degenerate IQ inputs.
* ``FEATURE_VECTOR_LENGTH`` -- fixed length of
  ``ClassificationResult.feature_vector``.
* ``CONFIDENCE_FLOOR`` -- below this the rules core returns
  ``"unknown"`` (Invariant B3 honesty).
* ``ModulationLabel`` -- the ``Literal`` type alias of valid labels.
* ``FamilyScores`` -- per-family score breakdown, returned by the
  rules layer for diagnostics.

WS-B-006 will add the modulation-class -> ``EmitterClass`` mapping
layer; this module deliberately does not re-export anything from
``rfmesh_contracts.enums`` -- the emitter-class semantic layer lives
one floor up.
"""

from rfmesh_ml.modulation_classifier import (
    CONFIDENCE_FLOOR,
    FEATURE_VECTOR_LENGTH,
    ClassificationResult,
    FamilyScores,
    FeatureExtractionError,
    ModulationClassifier,
    ModulationLabel,
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
