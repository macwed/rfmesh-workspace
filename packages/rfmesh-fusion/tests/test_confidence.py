"""Tests for the ADR-005 / ADR-009 confidence-band policy.

Pins the four-gate policy described in
``rfmesh_fusion.confidence`` module docstring:

* ``method == "fallback_centroid"`` short-circuits to LOW.
* ``range_m < 10 m`` short-circuits to LOW.
* All three of (GDOP <= threshold, semi/range <= 0.05,
  not is_outlier_any) pass: HIGH.
* Outlier-only failure: MEDIUM (the ADR-005 §D3 HIGH->MEDIUM
  downgrade -- not LOW).
* Exactly one of GDOP / ellipse fails: MEDIUM.
* Both GDOP and ellipse fail: LOW.

Also pins the load-bearing ``_HIGH_BAND_RANGE_FRACTION = 0.05``
constant -- ADR-005 §D5(a) says this value is binding.
"""

from __future__ import annotations

import pytest
from rfmesh_contracts.enums import ConfidenceLevel  # type: ignore[import-untyped, unused-ignore]
from rfmesh_fusion.confidence import (
    _HIGH_BAND_RANGE_FRACTION,
    compute_confidence_level,
)

# Default GDOP warn threshold from ``FusionConfig``. Re-derived at the
# test edge -- importing the contract field default would couple this
# test to the contracts package's defaults file.
_GDOP_THRESHOLD: float = 6.0

# ADR-005 §D5(a) binding value for ``_HIGH_BAND_RANGE_FRACTION``.
# Re-derived at the test edge so an accidental tweak to the policy
# constant trips the test (not the test's own copy of the constant).
_ADR_HIGH_BAND_FRACTION: float = 0.05


def test_confidence_policy_high_band_constant_is_load_bearing() -> None:
    """ADR-005 §D5(a) pins the constant value at 0.05.

    The comment block in ``confidence.py`` is itself binding per
    ADR-005 §D5(a); the value is half of that binding (the comment
    being the other half). Changing this constant is a one-line ADR
    amendment; the test asserts the v1.0 value to prevent silent
    churn.
    """
    assert _HIGH_BAND_RANGE_FRACTION == _ADR_HIGH_BAND_FRACTION


def test_confidence_policy_fallback_always_low() -> None:
    """``method == "fallback_centroid"`` returns LOW regardless of all else.

    The fallback path has no honest covariance / ellipse, so a HIGH
    or MEDIUM label would be a lie even when the placeholder ellipse
    happens to be small. The short-circuit at the top of
    ``compute_confidence_level`` guarantees the label.
    """
    # Geometrically perfect inputs -- would be HIGH if not fallback.
    result = compute_confidence_level(
        gdop=1.0,
        semi_major_m=10.0,
        range_m=10000.0,
        is_outlier_any=False,
        method="fallback_centroid",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result == ConfidenceLevel.LOW


def test_confidence_policy_range_below_10m_low() -> None:
    """``range_m < 10`` returns LOW regardless of other inputs (ADR-005 §Negative).

    The 5% threshold is meaningless when range_m collapses near zero;
    rather than invent a meaning, the policy labels LOW. The branch is
    reachable only on synthetic / pathological inputs (a real fix at
    < 10 m range would have gone through the fallback path).
    """
    result = compute_confidence_level(
        gdop=1.0,
        semi_major_m=0.01,
        range_m=5.0,
        is_outlier_any=False,
        method="stansfield+mle",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result == ConfidenceLevel.LOW


def test_confidence_policy_high_iff_all_three_pass() -> None:
    """Table-driven over the eight combinations of the three HIGH gates.

    Truth table (with method = "stansfield+mle", range_m large enough
    that the < 10 m short-circuit does not trip):

    | GDOP ok | ellipse ok | outlier ok | expected |
    |---------|------------|------------|----------|
    | T       | T          | T          | HIGH     |
    | T       | T          | F          | MEDIUM   | (outlier-only -> downgrade)
    | T       | F          | T          | MEDIUM   | (ellipse fails alone)
    | T       | F          | F          | MEDIUM   |
    | F       | T          | T          | MEDIUM   | (GDOP fails alone)
    | F       | T          | F          | MEDIUM   |
    | F       | F          | T          | LOW      | (both geometry axes fail)
    | F       | F          | F          | LOW      |
    """
    range_m = 1000.0
    semi_low = 40.0  # semi/range = 0.04 -- below 0.05, ellipse_ok = True
    semi_high = 100.0  # semi/range = 0.10 -- above 0.05, ellipse_ok = False
    gdop_ok_value = 3.0  # <= 6.0 threshold
    gdop_bad_value = 9.0  # > 6.0 threshold

    cases = [
        # (gdop, semi_major, is_outlier_any, expected)
        (gdop_ok_value, semi_low, False, ConfidenceLevel.HIGH),
        (gdop_ok_value, semi_low, True, ConfidenceLevel.MEDIUM),
        (gdop_ok_value, semi_high, False, ConfidenceLevel.MEDIUM),
        (gdop_ok_value, semi_high, True, ConfidenceLevel.MEDIUM),
        (gdop_bad_value, semi_low, False, ConfidenceLevel.MEDIUM),
        (gdop_bad_value, semi_low, True, ConfidenceLevel.MEDIUM),
        (gdop_bad_value, semi_high, False, ConfidenceLevel.LOW),
        (gdop_bad_value, semi_high, True, ConfidenceLevel.LOW),
    ]

    for gdop, semi, outlier, expected in cases:
        actual = compute_confidence_level(
            gdop=gdop,
            semi_major_m=semi,
            range_m=range_m,
            is_outlier_any=outlier,
            method="stansfield+mle",
            gdop_warn_threshold=_GDOP_THRESHOLD,
        )
        assert actual == expected, (
            f"compute_confidence_level(gdop={gdop}, semi={semi}, "
            f"outlier={outlier}) -> {actual}, expected {expected}. "
            f"semi/range={semi / range_m:.3f}, GDOP threshold={_GDOP_THRESHOLD}"
        )


def test_confidence_policy_outlier_downgrades_high_to_medium() -> None:
    """ADR-005 §D3 -- outlier-only failure downgrades HIGH -> MEDIUM, not to LOW.

    The fix is still real, just imperfect: the dashboard sees MEDIUM
    and the offending bearing's ``is_outlier`` flag is what the
    operator inspects to learn *why*. Going all the way to LOW would
    over-punish a single-bearing multipath event.
    """
    # All other gates pass -- only outlier flag flips.
    result_clean = compute_confidence_level(
        gdop=1.0,
        semi_major_m=10.0,
        range_m=1000.0,
        is_outlier_any=False,
        method="stansfield+mle",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result_clean == ConfidenceLevel.HIGH

    result_outlier = compute_confidence_level(
        gdop=1.0,
        semi_major_m=10.0,
        range_m=1000.0,
        is_outlier_any=True,
        method="stansfield+mle",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result_outlier == ConfidenceLevel.MEDIUM


def test_confidence_policy_low_when_gdop_above_threshold_only() -> None:
    """Failing GDOP alone produces MEDIUM, not LOW (HIGH gate fails on geometry only)."""
    result = compute_confidence_level(
        gdop=9.0,  # > threshold
        semi_major_m=10.0,  # semi/range = 0.01 -- ok
        range_m=1000.0,
        is_outlier_any=False,
        method="stansfield+mle",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result == ConfidenceLevel.MEDIUM


def test_confidence_policy_low_when_semi_over_range_above_threshold_only() -> None:
    """Failing ellipse alone produces MEDIUM (HIGH gate fails on ellipse only)."""
    result = compute_confidence_level(
        gdop=1.0,
        semi_major_m=100.0,  # semi/range = 0.10 -- above 0.05
        range_m=1000.0,
        is_outlier_any=False,
        method="stansfield+mle",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result == ConfidenceLevel.MEDIUM


def test_confidence_policy_low_when_both_high_gates_fail() -> None:
    """Failing both GDOP and ellipse produces LOW (both geometry axes weak)."""
    result = compute_confidence_level(
        gdop=9.0,
        semi_major_m=100.0,
        range_m=1000.0,
        is_outlier_any=False,
        method="stansfield+mle",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result == ConfidenceLevel.LOW


def test_confidence_policy_boundary_at_5_percent_exact() -> None:
    """``semi_major_m / range_m == 0.05`` exactly is ellipse-ok (``<=``, not ``<``).

    The threshold is inclusive: an ellipse exactly at 5% of range is
    HIGH-band-eligible. This is what the module docstring's "<="
    means; the test pins it so a future refactor that flipped to
    strict-less-than would surface here.
    """
    result = compute_confidence_level(
        gdop=1.0,
        semi_major_m=50.0,
        range_m=1000.0,  # ratio = 0.05 exactly
        is_outlier_any=False,
        method="stansfield+mle",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result == ConfidenceLevel.HIGH


def test_confidence_policy_boundary_at_gdop_threshold_exact() -> None:
    """``gdop == threshold`` exactly is geometry-ok (``<=``, not ``<``).

    Same inclusive-threshold logic as the ellipse boundary above. A
    fix at exactly GDOP 6.0 is HIGH-eligible, not pushed to MEDIUM.
    """
    result = compute_confidence_level(
        gdop=_GDOP_THRESHOLD,  # exactly at threshold
        semi_major_m=10.0,
        range_m=1000.0,
        is_outlier_any=False,
        method="stansfield+mle",
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result == ConfidenceLevel.HIGH


@pytest.mark.parametrize("method_tag", ["stansfield", "stansfield+mle"])
def test_confidence_policy_non_fallback_methods_can_reach_high(method_tag: str) -> None:
    """Both ``"stansfield"`` and ``"stansfield+mle"`` are HIGH-eligible.

    Only ``"fallback_centroid"`` short-circuits to LOW. The Stansfield-
    only method (when MLE refinement fails to converge) still produces
    a real covariance + ellipse from the seed, and the operator-facing
    label should reflect the geometry not the solver-step count.
    """
    result = compute_confidence_level(
        gdop=1.0,
        semi_major_m=10.0,
        range_m=1000.0,
        is_outlier_any=False,
        method=method_tag,
        gdop_warn_threshold=_GDOP_THRESHOLD,
    )
    assert result == ConfidenceLevel.HIGH
