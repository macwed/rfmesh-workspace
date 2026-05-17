"""ADR-005 confidence policy: map quantitative honesty payload to a band.

A ``FixEvent`` carries the full quantitative honesty payload (covariance,
ellipse, GDOP, residuals, method). The operator-facing band
(``HIGH/MEDIUM/LOW``) is the discretized display of that payload, derived
by a single function in this module so the policy lives in one place.

The policy is fixed by ``ADR-005-fusion-confidence-policy.md`` §D1-D6 and
amended (for narrative interpretation only -- the threshold value stays
the same) by ``ADR-009-confidence-band-math-correction-and-demo-narrative
.md``.

Inputs to ``compute_confidence_level``:

* ``gdop`` -- the dimensionless geometry-dilution scalar from
  ``gdop.compute_gdop``. Compared against
  ``FusionConfig.gdop_warn_threshold`` (default 6.0).
* ``semi_major_m`` -- the 95% confidence ellipse semi-major axis from
  ``covariance.covariance_to_ellipse``. Compared against
  ``_HIGH_BAND_RANGE_FRACTION * range_m``.
* ``range_m`` -- Euclidean ENU distance from the solved emitter to the
  node centroid (ADR-005 §D2). The denominator of the
  semi-major / range ratio.
* ``is_outlier_any`` -- ``True`` iff any contributing bearing has
  ``residuals.ResidualsResult.is_outlier[i] == True`` (ADR-005 §D3).
  A True value downgrades an otherwise-HIGH fix to MEDIUM -- not LOW,
  because the fix is still real, just imperfect.
* ``method`` -- the solver's method tag. ``"fallback_centroid"``
  short-circuits to LOW per ADR-005 §D4 boundary case.
* ``gdop_warn_threshold`` -- the threshold from
  ``FusionConfig.gdop_warn_threshold``. Passed in rather than imported
  from contracts to keep the function pure / config-injectable; the
  ``Fuser`` orchestrator extracts it from its config.

The four conditions for HIGH (all must hold):

1. ``method != "fallback_centroid"`` -- a fallback fix is honest about
   not having a covariance.
2. ``range_m >= 10.0`` -- the operational tolerance collapses near
   zero range (ADR-005 §"Negative" bullet); we refuse to label
   anything degenerate as HIGH.
3. ``gdop <= gdop_warn_threshold`` -- the geometric layout is not weak.
4. ``semi_major_m / range_m <= _HIGH_BAND_RANGE_FRACTION`` -- the
   ellipse is operationally tight (5% of standoff range).
5. ``not is_outlier_any`` -- no contributing bearing is a per-bearing
   3-sigma outlier (ADR-005 §D3).

If conditions 1-2 fail: LOW. Otherwise, count which of (3, 4, 5) fail:

* All pass: HIGH.
* Only (5) fails: MEDIUM (the ADR-005 §D3 HIGH->MEDIUM downgrade --
  the fix is real, just imperfect).
* Exactly one of (3) or (4) fails (with (5) passing): MEDIUM.
* (3) fails AND (4) fails: LOW (both axes of geometry are weak --
  this is the genuine MEDIUM-vs-LOW boundary).
* (5) fails in combination with any of (3, 4): MEDIUM (the outlier
  downgrade never produces LOW on its own, but a fix that was already
  going to be MEDIUM stays MEDIUM).

References
----------
* ``docs/adr/ADR-005-fusion-confidence-policy.md`` -- D1-D6, the
  operational tolerance value (5%), the residual gate, the range_m
  denominator, the boundary cases, the visibility obligations.
* ``docs/adr/ADR-009-confidence-band-math-correction-and-demo-narrative.md``
  -- the 1-sigma vs 95% scale correction. The threshold value (0.05)
  stays the same; the demo narrative says "L1-only mesh stays in
  MEDIUM; HIGH band is the denser-deployment future state". The
  ``test_fuser_trench_demo_beat_d_high_band_NOT_reached`` test pins
  this at the implementation level.
* ``INTERFACES.md`` §3 -- ``FixEvent.confidence_level`` semantics.
"""

from __future__ import annotations

from rfmesh_contracts.enums import ConfidenceLevel  # type: ignore[import-untyped, unused-ignore]

# Operational threshold for the HIGH confidence band -- NOT the
# BoTH3 Counter-Jamming Challenge 2 spec tolerance.
#
# BoTH3 spec:                     <= 20 m at 2-5 km   =>  0.4-1.0 %.
# Our HIGH-band operational gate: <= 5 % of range.
#
# A "HIGH" label means "well above the noise floor of usable fixes",
# not "competition-compliant". The dashboard surfaces the actual
# percentage (see ADR-005 D5(b)) so the operator and a jury can see
# the spec-compliant regime (< 1 %) cross independently of the band.
#
# This comment is binding per ADR-005 §D5(a) -- Claude Code is *not*
# free to remove or paraphrase it in subsequent ticket churn.
_HIGH_BAND_RANGE_FRACTION: float = 0.05

# Degenerate-range cutoff per ADR-005 §"Negative" bullet. Below this
# the semi-major / range ratio collapses arithmetically (and the
# operator is anyway not interested in "the emitter is on top of a
# node" as a precision metric). The Fuser only ever reaches this
# branch on synthetic inputs or on a node-on-emitter pathology -- the
# real-world case goes through the fallback path long before.
_MIN_RANGE_M_FOR_HIGH: float = 10.0

# The fallback-method tag the Fuser sets when ``stansfield_seed``
# raises ``DegenerateGeometryError`` (ADR-007 D3). Centralised here so
# the policy and the orchestrator agree on the spelling; a typo'd
# string would silently break the LOW short-circuit.
_FALLBACK_METHOD: str = "fallback_centroid"


def compute_confidence_level(
    *,
    gdop: float,
    semi_major_m: float,
    range_m: float,
    is_outlier_any: bool,
    method: str,
    gdop_warn_threshold: float,
) -> ConfidenceLevel:
    """Return the ADR-005 / ADR-009 confidence band for a fix.

    Parameters
    ----------
    gdop
        The dimensionless geometry-dilution scalar from ``compute_gdop``.
    semi_major_m
        The 95% ellipse semi-major axis from ``covariance_to_ellipse``,
        in metres.
    range_m
        Euclidean ENU distance from the solved emitter to the node
        centroid (ADR-005 §D2), in metres.
    is_outlier_any
        ``True`` iff any contributing bearing has ``is_outlier == True``
        per ``residuals.compute_residuals``.
    method
        The solver method tag the Fuser will write into
        ``FixEvent.method``. ``"fallback_centroid"`` short-circuits to
        LOW (ADR-005 §D4).
    gdop_warn_threshold
        The threshold above which GDOP is considered weak, passed in
        from ``FusionConfig.gdop_warn_threshold`` (default 6.0).
        Injected rather than imported so the policy stays pure /
        testable in isolation.

    Returns
    -------
    ConfidenceLevel
        ``HIGH``, ``MEDIUM``, or ``LOW`` per the ADR-005 / ADR-009
        policy described in the module docstring.

    Notes
    -----
    The function performs no validation of input ranges (negative
    sigmas, NaN GDOP, etc.) -- those are upstream caller bugs and the
    ``Fuser`` orchestrator does not produce them. Defensive checks
    here would mask a real bug, not catch one.
    """
    # D4 short-circuit: fallback_centroid is always LOW regardless of
    # every other input. The covariance / ellipse on this path is a
    # placeholder ("we have no honest covariance"); labelling it
    # MEDIUM or HIGH would defeat the whole point of having the
    # fallback path.
    if method == _FALLBACK_METHOD:
        return ConfidenceLevel.LOW

    # ADR-005 §"Negative" bullet: range_m near zero makes the 5%
    # threshold arithmetically meaningless. We label LOW rather than
    # invent a meaning. This branch is reachable only on synthetic /
    # pathological inputs (a real fix at <10 m range would have gone
    # through the fallback path).
    if range_m < _MIN_RANGE_M_FOR_HIGH:
        return ConfidenceLevel.LOW

    # The three HIGH-band gates per ADR-005 §D1 / §D3.
    geometry_ok = gdop <= gdop_warn_threshold
    ellipse_ok = (semi_major_m / range_m) <= _HIGH_BAND_RANGE_FRACTION
    outlier_ok = not is_outlier_any

    if geometry_ok and ellipse_ok and outlier_ok:
        return ConfidenceLevel.HIGH

    # Below HIGH: decide MEDIUM vs LOW. Per the module docstring:
    # * Outlier-only failure: MEDIUM (the ADR-005 §D3 HIGH->MEDIUM
    #   downgrade is binding; the fix is real, just imperfect).
    # * Both GDOP and ellipse fail: LOW (both axes of geometry weak).
    # * Exactly one of GDOP / ellipse fails (with or without outlier
    #   flag): MEDIUM.
    if not geometry_ok and not ellipse_ok:
        return ConfidenceLevel.LOW

    return ConfidenceLevel.MEDIUM
