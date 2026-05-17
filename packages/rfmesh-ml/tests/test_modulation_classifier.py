"""Acceptance tests for the WS-B-005 modulation-class classifier.

21 tests total:

* 12 high-SNR positives (4 classes x 3 SNRs in {5, 10, 20} dB), each
  asserts correct ``modulation_class`` and ``confidence >= 0.7``.
* 4 low-SNR negatives (4 classes at SNR=-5 dB), each asserts
  ``modulation_class == "unknown"``.
* 1 pure-noise test, asserts ``"unknown"`` and ``confidence < 0.5``.
* 4 structural / hygiene tests on the public API surface.

Determinism: every test uses ``numpy.random.default_rng(seed=42)`` via
the fixture factories in ``conftest.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from typing import Literal

import numpy as np
import numpy.typing as npt
import pytest
from rfmesh_ml import (
    CONFIDENCE_FLOOR,
    FEATURE_VECTOR_LENGTH,
    ClassificationResult,
    FeatureExtractionError,
    ModulationClassifier,
)

_HIGH_SNRS_DB: tuple[float, ...] = (5.0, 10.0, 20.0)
_HIGH_CONFIDENCE_FLOOR: float = 0.7
_LOW_SNR_DB: float = -5.0
_PURE_NOISE_CONFIDENCE_MAX: float = 0.5
"""Ceiling on the classifier's confidence when handed pure complex
Gaussian noise. The per-file-ignores in workspace ruff config could
suppress PLR2004 (magic-value-in-comparison) for tests, but naming the
constant explicitly is the convention adopted across rfmesh-fusion and
rfmesh-sdr test trees -- and it makes the test more readable."""


# ---------------------------------------------------------------------------
# Structural / shape tests
# ---------------------------------------------------------------------------


def test_classification_result_shape(
    cw_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    sample_rate_hz: float,
) -> None:
    """``ClassificationResult`` exposes the three fields the contract names."""
    iq = cw_iq_factory(20.0, 42)
    result = ModulationClassifier().classify(iq, fs=sample_rate_hz)

    assert isinstance(result, ClassificationResult)
    assert result.modulation_class in {"cw", "fhss", "fsk", "lora", "unknown"}
    assert isinstance(result.confidence, float)
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.feature_vector, np.ndarray)
    assert result.feature_vector.dtype == np.float64
    assert result.feature_vector.ndim == 1
    assert result.feature_vector.size == FEATURE_VECTOR_LENGTH
    # Frozen dataclass -- mutation raises.
    with pytest.raises((AttributeError, TypeError)):
        result.modulation_class = "fhss"  # type: ignore[misc]


def test_iq_dtype_complex64_strict(sample_rate_hz: float) -> None:
    """Passing complex128 must raise TypeError naming complex64."""
    classifier = ModulationClassifier()
    iq_wrong = np.ones(2048, dtype=np.complex128)
    with pytest.raises(TypeError, match="complex64"):
        classifier.classify(iq_wrong, fs=sample_rate_hz)  # type: ignore[arg-type]


def test_iq_empty_raises(sample_rate_hz: float) -> None:
    """Empty IQ block raises a feature-extraction error with a non-empty message."""
    classifier = ModulationClassifier()
    empty = np.zeros(0, dtype=np.complex64)
    with pytest.raises(FeatureExtractionError) as excinfo:
        classifier.classify(empty, fs=sample_rate_hz)
    assert str(excinfo.value)


def test_feature_vector_is_finite(
    cw_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    fhss_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    fsk_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    lora_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    noise_iq_factory: Callable[[int], npt.NDArray[np.complex64]],
    sample_rate_hz: float,
) -> None:
    """No NaN / inf in the feature vector across the six canonical scenarios."""
    classifier = ModulationClassifier()
    scenarios = [
        cw_iq_factory(5.0, 42),
        fhss_iq_factory(5.0, 42),
        fsk_iq_factory(5.0, 42),
        lora_iq_factory(5.0, 42),
        cw_iq_factory(-5.0, 42),
        noise_iq_factory(42),
    ]
    for iq in scenarios:
        result = classifier.classify(iq, fs=sample_rate_hz)
        assert np.all(np.isfinite(result.feature_vector)), (
            "feature_vector contains NaN or inf -- numerical hygiene gate failed"
        )


# ---------------------------------------------------------------------------
# 12 high-SNR positive tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("snr_db", _HIGH_SNRS_DB)
def test_classify_cw_high_snr(
    snr_db: float,
    cw_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    sample_rate_hz: float,
) -> None:
    """CW @ SNR in {5, 10, 20} dB -> 'cw' with confidence >= 0.7."""
    classifier = ModulationClassifier()
    iq = cw_iq_factory(snr_db, 42)
    result = classifier.classify(iq, fs=sample_rate_hz)
    assert result.modulation_class == "cw", (
        f"expected 'cw' at SNR={snr_db} dB, got {result.modulation_class!r} "
        f"(confidence={result.confidence:.3f})"
    )
    assert result.confidence >= _HIGH_CONFIDENCE_FLOOR, (
        f"confidence {result.confidence:.3f} below {_HIGH_CONFIDENCE_FLOOR} at SNR={snr_db} dB"
    )


@pytest.mark.parametrize("snr_db", _HIGH_SNRS_DB)
def test_classify_fhss_high_snr(
    snr_db: float,
    fhss_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    sample_rate_hz: float,
) -> None:
    """FHSS @ SNR in {5, 10, 20} dB -> 'fhss' with confidence >= 0.7."""
    classifier = ModulationClassifier()
    iq = fhss_iq_factory(snr_db, 42)
    result = classifier.classify(iq, fs=sample_rate_hz)
    assert result.modulation_class == "fhss", (
        f"expected 'fhss' at SNR={snr_db} dB, got {result.modulation_class!r} "
        f"(confidence={result.confidence:.3f})"
    )
    assert result.confidence >= _HIGH_CONFIDENCE_FLOOR


@pytest.mark.parametrize("snr_db", _HIGH_SNRS_DB)
def test_classify_fsk_high_snr(
    snr_db: float,
    fsk_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    sample_rate_hz: float,
) -> None:
    """2-FSK @ SNR in {5, 10, 20} dB -> 'fsk' with confidence >= 0.7."""
    classifier = ModulationClassifier()
    iq = fsk_iq_factory(snr_db, 42)
    result = classifier.classify(iq, fs=sample_rate_hz)
    assert result.modulation_class == "fsk", (
        f"expected 'fsk' at SNR={snr_db} dB, got {result.modulation_class!r} "
        f"(confidence={result.confidence:.3f})"
    )
    assert result.confidence >= _HIGH_CONFIDENCE_FLOOR


@pytest.mark.parametrize("snr_db", _HIGH_SNRS_DB)
def test_classify_lora_high_snr(
    snr_db: float,
    lora_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    sample_rate_hz: float,
) -> None:
    """LoRa @ SNR in {5, 10, 20} dB -> 'lora' with confidence >= 0.7."""
    classifier = ModulationClassifier()
    iq = lora_iq_factory(snr_db, 42)
    result = classifier.classify(iq, fs=sample_rate_hz)
    assert result.modulation_class == "lora", (
        f"expected 'lora' at SNR={snr_db} dB, got {result.modulation_class!r} "
        f"(confidence={result.confidence:.3f})"
    )
    assert result.confidence >= _HIGH_CONFIDENCE_FLOOR


# ---------------------------------------------------------------------------
# 4 low-SNR 'unknown' tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory_name",
    ["cw_iq_factory", "fhss_iq_factory", "fsk_iq_factory", "lora_iq_factory"],
)
def test_classify_unknown_low_snr(
    factory_name: str,
    cw_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    fhss_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    fsk_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    lora_iq_factory: Callable[[float, int], npt.NDArray[np.complex64]],
    sample_rate_hz: float,
) -> None:
    """SNR = -5 dB -> 'unknown' for every modulation class.

    Honesty rule (Invariant B3 + WS-B-005 acceptance criterion 5): the
    classifier does not fabricate a confident wrong label at low SNR.
    A fabricated label poisons fusion downstream by inducing spurious
    EmitterClass tags on bearings.
    """
    factories: dict[str, Callable[[float, int], npt.NDArray[np.complex64]]] = {
        "cw_iq_factory": cw_iq_factory,
        "fhss_iq_factory": fhss_iq_factory,
        "fsk_iq_factory": fsk_iq_factory,
        "lora_iq_factory": lora_iq_factory,
    }
    iq = factories[factory_name](_LOW_SNR_DB, 42)
    classifier = ModulationClassifier()
    result = classifier.classify(iq, fs=sample_rate_hz)
    assert result.modulation_class == "unknown", (
        f"{factory_name} at SNR={_LOW_SNR_DB} dB: expected 'unknown', got "
        f"{result.modulation_class!r} (confidence={result.confidence:.3f}) -- "
        "honesty rule failed; the classifier fabricated a confident wrong label"
    )
    # When unknown, confidence is the un-scaled max score, which must be
    # below the floor by definition.
    assert result.confidence < CONFIDENCE_FLOOR


# ---------------------------------------------------------------------------
# Pure-noise test
# ---------------------------------------------------------------------------


def test_classify_unknown_pure_noise(
    noise_iq_factory: Callable[[int], npt.NDArray[np.complex64]],
    sample_rate_hz: float,
) -> None:
    """Pure complex Gaussian noise -> 'unknown' with confidence < 0.5."""
    classifier = ModulationClassifier()
    iq = noise_iq_factory(42)
    result = classifier.classify(iq, fs=sample_rate_hz)
    assert result.modulation_class == "unknown", (
        f"pure noise was classified as {result.modulation_class!r} "
        f"(confidence={result.confidence:.3f}) -- a fabricated label on "
        "noise alone violates Invariant B3"
    )
    assert result.confidence < _PURE_NOISE_CONFIDENCE_MAX


# ---------------------------------------------------------------------------
# CNN-backend negative-path test
# ---------------------------------------------------------------------------


def test_cnn_backend_raises_without_torch() -> None:
    """``backend='cnn'`` raises ImportError when torch is not installed.

    Skipped automatically when torch IS installed -- the positive path
    (constructor succeeds, classify raises NotImplementedError because
    the v1.0 CNN backend is a training-only skeleton) is exercised in
    a dedicated test if/when torch becomes a runtime dep.
    """
    if importlib.util.find_spec("torch") is not None:
        pytest.skip("torch is installed; this test exercises the no-torch negative path")
    with pytest.raises(ImportError, match="torch"):
        ModulationClassifier(backend="cnn")


def test_rules_backend_does_not_import_torch() -> None:
    """The ``backend='rules'`` path must not bring torch into the import graph.

    Structural guard for the lazy-import contract -- if a future change
    accidentally puts ``import torch`` at module top-level in
    ``modulation_classifier.py`` or any module the rules path
    transitively imports, this test catches it at the import level.
    """
    # Build a rules-backend classifier; the rules path must not have
    # caused a torch import.
    ModulationClassifier(backend="rules")
    assert "torch" not in sys.modules, (
        "torch was imported by the rules-backend path; the lazy-import "
        "contract requires torch to remain absent from the import graph "
        "when backend='rules'."
    )


def test_backend_invalid_raises() -> None:
    """Invalid backend string raises ValueError."""
    invalid_backend: Literal["rules", "cnn"] = "totally-not-a-backend"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="backend"):
        ModulationClassifier(backend=invalid_backend)
