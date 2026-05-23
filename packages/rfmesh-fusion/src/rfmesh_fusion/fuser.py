"""``StansfieldMLEFuser`` -- the `Fuser` Protocol implementation.

This is the wiring ticket. The pure modules ``projection``, ``geometry``,
``stansfield``, ``mle``, ``covariance``, ``gdop``, ``residuals`` and
``confidence`` each carry one piece of the cross-fix arithmetic; this
class is the orchestrator that turns a batch of
``BearingReport`` s into a single ``FixEvent`` (or ``None``) with the
full honesty payload per HANDOFF §0 Advantage #6.

ALGORITHM (binding sequence, ADR-007 + ADR-005 + ADR-009)
---------------------------------------------------------
1. Materialise the input ``Iterable`` to a tuple, deterministic order.
2. Filter to bearings inside a ``batch_window_ms``-wide window centred
   on the *median* ``t_unix_ns`` -- so a stale or future-stamped bearing
   in the batch does not poison the fix.
3. If fewer than ``config.min_bearings_for_fix`` survive: return
   ``None`` (the Protocol's "cannot responsibly solve" signal).
4. Choose ENU origin as ``projection.choose_enu_origin`` of the
   contributing nodes' geodetic positions. Project each
   ``node_position`` to ENU.
5. Try ``stansfield.stansfield_seed`` for the closed-form seed.
   * On ``DegenerateGeometryError``: take the fallback-centroid path
     (ADR-007 D3) -- pairwise ray-ray crossings, weighted by sum of
     contributing weights, centroid of those crossings. If no usable
     crossings: return ``None``. Method = ``"fallback_centroid"``,
     placeholder ellipse (we have no honest covariance), confidence
     LOW.
6. Else try ``mle.solve_mle`` seeded by Stansfield.
   * On ``MLEConvergenceError``: keep the Stansfield seed, method =
     ``"stansfield"`` (the seed is consistent but finite-sample-biased
     -- the operator sees the honest seed, not a fabricated converged
     position).
   * Else use the MLE-converged position, method = ``"stansfield+mle"``.
7. Compute covariance via ``covariance.compute_covariance`` and
   ellipse via ``covariance.covariance_to_ellipse``.
   * On ``SingularFisherInformationError`` (should not happen
     post-MLE, but defensive): downgrade to Stansfield-only method
     with the placeholder ellipse.
8. Compute GDOP via ``gdop.compute_gdop`` at the chosen emitter.
9. Compute residuals via ``residuals.compute_residuals``.
10. Compute ``range_m`` = ``hypot(emitter_xy)`` (origin is the centroid
    by construction, so ``emitter_xy`` is the displacement from
    centroid; the magnitude is the standoff range ADR-005 §D2 uses).
11. Compute ``confidence_level`` via ``compute_confidence_level``.
12. Compute consensus ``emitter_class`` across contributing bearings.
13. Reproject emitter ENU -> geodetic via ``projection.from_enu`` with
    ``sigma_m = ellipse.semi_major_m`` (the scalar convenience summary
    INTERFACES.md §2 specifies; the ellipse is authoritative).
14. ``t_unix_ns`` = arithmetic-mean midpoint of contributing bearings'
    timestamps, computed via integer division to preserve nanosecond
    resolution end-to-end (HANDOFF §0 / INTERFACES.md §0).
15. Construct ``FixEvent`` with a fresh ``uuid.uuid4()`` ``fix_id`` and
    return.

WHY ``__init__(config)`` AND ``fuse(bearings, config)`` BOTH TAKE config
-----------------------------------------------------------------------
The ``Fuser`` Protocol's ``fuse`` signature takes ``(bearings, config)``
because the protocol is stateless w.r.t. config -- a single ``Fuser``
implementation might serve multiple deployments with different settings.
The class also takes ``config`` in ``__init__`` for the common case
where one instance serves one deployment; the per-call ``config`` over-
rides this default. This means the same instance correctly satisfies
``isinstance(fuser, Fuser)`` (Protocol check) AND has the ergonomic
"construct from a config, then call fuse(bearings)" usage.

WHAT IS NOT HERE (delegated)
----------------------------
* The Stansfield / MLE / covariance / GDOP / residuals arithmetic --
  upstream modules, this file orchestrates.
* The confidence policy mapping -- ``confidence.py``.
* The ENU<->WGS-84 projection -- ``projection.py``.
* The fallback-centroid arithmetic itself -- ``geometry.
  weighted_centroid_of_crossings``.

References
----------
* ``INTERFACES.md`` §3 (``FixEvent`` -- every field and the
  contributing_nodes <-> residuals_deg ordering invariant).
* ``INTERFACES.md`` §5 (``Fuser`` Protocol -- the behavioural contract).
* ``docs/adr/ADR-005-fusion-confidence-policy.md`` (the band policy
  this orchestrator routes through ``compute_confidence_level``).
* ``docs/adr/ADR-007-fusion-algorithm-choices.md`` D1-D5 (the algorithm
  spine: Stansfield + MLE + fallback + unweighted GDOP + closed-form
  ellipse).
* ``docs/adr/ADR-009-confidence-band-math-correction-and-demo-narrative.md``
  (the demo lives in MEDIUM, not HIGH -- the trench-demo test pins
  this).
* ``HANDOFF_TO_CLAUDE_CODE_LEAD.md`` §0 Advantage #6 (honesty payload),
  §2 B1 (contracts frozen), B3 (no silent fallbacks), B4 (demo
  honesty), B5 (pure fusion).
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

import numpy as np
from rfmesh_contracts.config import FusionConfig  # type: ignore[import-untyped, unused-ignore]
from rfmesh_contracts.enums import (  # type: ignore[import-untyped, unused-ignore]
    Capability,
    EmitterClass,
)
from rfmesh_contracts.geospatial import EllipseENU  # type: ignore[import-untyped, unused-ignore]
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
    FixEvent,
)

from .confidence import compute_confidence_level
from .covariance import compute_covariance, covariance_to_ellipse
from .exceptions import (
    DegenerateGeometryError,
    MLEConvergenceError,
    SingularFisherInformationError,
)
from .gdop import compute_gdop
from .geometry import bearing_to_unit_vector, ray_ray_crossing, weighted_centroid_of_crossings
from .mle import solve_mle
from .projection import choose_enu_origin, from_enu, to_enu
from .residuals import compute_residuals
from .stansfield import stansfield_seed

# Method tags written into ``FixEvent.method``. Pinned as module
# constants so a typo would surface at import time, not as a silent
# mismatch with the confidence policy's ``_FALLBACK_METHOD``.
_METHOD_STANSFIELD: Final[str] = "stansfield"
_METHOD_STANSFIELD_MLE: Final[str] = "stansfield+mle"
_METHOD_FALLBACK_CENTROID: Final[str] = "fallback_centroid"

# Placeholder ellipse on the fallback path. We have no honest
# covariance to derive the 95% contour from; producing a meaningful
# number would be a lie. We carry a generous-but-finite ellipse and
# rely on the ``confidence_level = LOW`` label to communicate "this
# is a degenerate fix" -- per ADR-005 §D4. The semi-axis is half the
# standoff range, which is large enough that no operator will action
# the fix alone but small enough that the ellipse renders on the
# dashboard without overflowing the map view.
_FALLBACK_ELLIPSE_RANGE_FRACTION: Final[float] = 0.5
_MIN_FALLBACK_SEMI_M: Final[float] = 1.0


class StansfieldMLEFuser:
    """``Fuser`` Protocol implementation: Stansfield seed + MLE refinement.

    Construct with a default ``FusionConfig``; ``fuse`` accepts a
    per-call ``FusionConfig`` that overrides the default. The same
    instance can therefore serve multiple deployments while still
    satisfying ``isinstance(fuser, Fuser)`` -- the Protocol is
    ``@runtime_checkable`` and structural conformance is the only
    requirement.

    The implementation is **pure** (Invariant B5): no network, no file
    I/O, no subprocess, no SDR access. The only non-determinism is the
    ``uuid.uuid4()`` call for ``fix_id`` -- which is required by
    ``FixEvent`` to be unique per fix.

    Examples
    --------
    >>> config = FusionConfig(listen_url="udp://0.0.0.0:9000")
    >>> fuser = StansfieldMLEFuser(config)
    >>> fix = fuser.fuse(bearings, config)  # type: ignore[name-defined]
    """

    def __init__(self, config: FusionConfig) -> None:
        """Construct with a default ``FusionConfig``.

        The default is used when callers invoke ``fuse(bearings, config)``
        with the same config -- a common case. Per-call config still
        wins; the constructor argument is the convenience default,
        not state the fuser mutates over time.
        """
        self._default_config = config

    def fuse(
        self,
        bearings: Iterable[BearingReport],
        config: FusionConfig | None = None,
    ) -> FixEvent | None:
        """Cross-fix a batch of time-windowed bearings into a ``FixEvent``.

        The per-call ``config`` overrides ``__init__``'s default; if
        ``None``, the default is used.

        Returns ``None`` -- not a fabricated fix -- when:

        * Fewer than ``config.min_bearings_for_fix`` bearings inside
          the time window.
        * Fallback-centroid path itself produces no usable crossing
          (every pair of bearing lines is parallel).

        A returned ``FixEvent`` always carries the full honesty payload
        with the confidence band labelled per ADR-005 / ADR-009. A
        weak-but-real fix is *labelled* weak (``confidence_level =
        LOW``), not withheld.
        """
        active_config = config if config is not None else self._default_config

        # Drop L1 refusal events (ADR-013 G4): they are wire-level
        # surfaces of "the estimator declined to emit a bearing" and
        # must never contribute weight to a fix. They reach the fuser
        # only so a remote dashboard can render the refusal; here they
        # are stripped before the time-window + min-count check.
        bearings_tuple = tuple(
            b for b in bearings if b.method is not Capability.L1_REFUSED_PROMINENCE
        )
        windowed = _filter_to_time_window(bearings_tuple, active_config.batch_window_ms)
        if len(windowed) < active_config.min_bearings_for_fix:
            return None

        # ENU frame: origin at the centroid of contributing node
        # positions (ADR-005 §D2 / projection.choose_enu_origin docs).
        # Each bearing's node_position is projected once; the order
        # is preserved end-to-end so residuals_deg[i] <-> bearings[i]
        # <-> contributing_nodes[i] (INTERFACES.md §3 invariant).
        origin = choose_enu_origin(b.node_position for b in windowed)
        node_positions_enu = tuple(to_enu(b.node_position, origin) for b in windowed)

        emitter_xy, method, covariance_2x2 = _solve_emitter_position(windowed, node_positions_enu)

        ellipse: EllipseENU
        if method == _METHOD_FALLBACK_CENTROID:
            # Fallback path produced no usable crossings.
            if emitter_xy is None:
                return None
            ellipse = _placeholder_ellipse(emitter_xy)
        else:
            assert emitter_xy is not None
            maybe_ellipse = _ellipse_or_downgrade(covariance_2x2)
            if maybe_ellipse is None:
                # Defensive: covariance singular post-MLE. Downgrade to
                # Stansfield-only method with a placeholder ellipse so
                # the FixEvent still publishes (with LOW band).
                method = _METHOD_STANSFIELD
                ellipse = _placeholder_ellipse(emitter_xy)
            else:
                ellipse = maybe_ellipse

        residuals_result = compute_residuals(emitter_xy, windowed, node_positions_enu)
        gdop_uncomputable_reason: str | None = None
        try:
            gdop_value = compute_gdop(emitter_xy, node_positions_enu)
        except DegenerateGeometryError as exc:
            # If GDOP cannot be computed (collinear nodes through the
            # emitter), label the fix LOW and synthesise a generous
            # GDOP placeholder. The contract requires ``gdop > 0``
            # strictly; we use a large finite value to mark "geometry
            # is weak" -- the confidence_level will be LOW anyway via
            # the gdop_warn_threshold gate. The reason string is
            # surfaced on FixEvent.gdop_uncomputable_reason so the
            # dashboard renders "GDOP: uncomputable (<reason>)"
            # instead of treating the sentinel as a real measurement
            # (ADR-013 G3).
            gdop_value = max(active_config.gdop_warn_threshold * 10.0, 1.0)
            gdop_uncomputable_reason = str(exc) or "degenerate geometry"

        range_m = math.hypot(emitter_xy[0], emitter_xy[1])
        is_outlier_any = any(residuals_result.is_outlier)

        confidence = compute_confidence_level(
            gdop=gdop_value,
            semi_major_m=ellipse.semi_major_m,
            range_m=range_m,
            is_outlier_any=is_outlier_any,
            method=method,
            gdop_warn_threshold=active_config.gdop_warn_threshold,
        )

        emitter_class = _consensus_emitter_class(windowed)

        position = from_enu(
            emitter_xy[0],
            emitter_xy[1],
            origin=origin,
            sigma_m=ellipse.semi_major_m,
        )

        return FixEvent(
            fix_id=uuid.uuid4(),
            t_unix_ns=_midpoint_t_unix_ns(windowed),
            position=position,
            covariance_m2=(
                float(covariance_2x2[0]),
                float(covariance_2x2[1]),
                float(covariance_2x2[2]),
            ),
            confidence_ellipse_95=ellipse,
            confidence_level=confidence,
            contributing_nodes=residuals_result.contributing_node_ids,
            residuals_deg=residuals_result.residuals_deg,
            gdop=gdop_value,
            method=method,
            emitter_class=emitter_class,
            gdop_uncomputable_reason=gdop_uncomputable_reason,
        )


# ---------------------------------------------------------------------------
# Module-private helpers. These are the orchestration internals -- not
# re-exported from ``__init__.py``. Keeping them at module scope (not
# methods on the class) makes each one independently unit-testable and
# avoids implicit ``self``-state coupling.
# ---------------------------------------------------------------------------


def _filter_to_time_window(
    bearings: Sequence[BearingReport],
    batch_window_ms: float,
) -> tuple[BearingReport, ...]:
    """Keep bearings whose ``t_unix_ns`` is within ``batch_window_ms / 2`` of the median.

    Why median, not mean: a single stale bearing in the batch would
    drag a mean window centre off the live cluster, potentially
    excluding the live bearings instead of the stale one. The median
    is robust against this exact failure mode.

    Why window centred on median, not on max: the operator's
    interpretation of a fix is "what was the situation at time T?".
    Anchoring T to the median means the fix's ``t_unix_ns`` is
    representative of the cluster, even when one bearing arrived
    early or late.

    An empty input returns an empty tuple. A single-bearing input
    returns it unchanged (median is the one timestamp; the window is
    trivially satisfied).
    """
    if not bearings:
        return ()

    timestamps_ns = [b.t_unix_ns for b in bearings]
    # ``statistics.median`` of an integer sequence may return a float
    # (when the length is even, the mean of the two middle elements).
    # We compare in float to keep the half-window arithmetic uniform.
    median_ns = float(statistics.median(timestamps_ns))
    half_window_ns = (batch_window_ms * 1_000_000.0) / 2.0

    return tuple(b for b in bearings if abs(float(b.t_unix_ns) - median_ns) <= half_window_ns)


def _solve_emitter_position(
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float] | None, str, tuple[float, float, float]]:
    """Return ``(emitter_xy_or_None, method_tag, covariance_tuple)``.

    Drives the Stansfield -> MLE -> fallback decision tree per ADR-007
    D2 / D3. The returned ``covariance_tuple`` is
    ``(sigma_xx, sigma_xy, sigma_yy)`` -- the upper triangle of the 2x2
    matrix in the order ``FixEvent.covariance_m2`` expects. On the
    fallback path it is ``(0.0, 0.0, 0.0)``: the placeholder ellipse
    carries the "we don't know" signal via its area + LOW band, not
    via a fabricated covariance. The caller (the ``fuse`` method) then
    builds the ellipse, residuals, GDOP, and confidence on top.

    Returning ``None`` for the emitter signals "even the fallback path
    has no answer" (every pair of rays parallel) -- the ``fuse`` method
    propagates this as a ``None`` ``FixEvent``.
    """
    try:
        seed_xy = stansfield_seed(bearings, node_positions_enu)
    except DegenerateGeometryError:
        fallback_xy = _fallback_centroid(bearings, node_positions_enu)
        return (fallback_xy, _METHOD_FALLBACK_CENTROID, (0.0, 0.0, 0.0))

    try:
        mle_result = solve_mle(bearings, node_positions_enu, seed_xy)
        emitter_xy = mle_result.position
        method = _METHOD_STANSFIELD_MLE
    except MLEConvergenceError:
        # MLE failed to converge; fall back to the Stansfield seed.
        # The seed is finite-sample biased but consistent -- the
        # honest answer when refinement does not land. The covariance
        # is then computed at the seed, not at a fabricated MLE
        # output; this is honest about what we did and didn't do.
        emitter_xy = seed_xy
        method = _METHOD_STANSFIELD

    try:
        cov_matrix = compute_covariance(emitter_xy, bearings, node_positions_enu)
        cov_tuple = (
            float(cov_matrix[0, 0]),
            float(cov_matrix[0, 1]),
            float(cov_matrix[1, 1]),
        )
    except SingularFisherInformationError:
        # Defensive: should not happen post-MLE-converged because the
        # Stansfield seed would have raised first on the same input.
        # Carry a zero covariance and let the caller substitute the
        # placeholder ellipse + downgrade method to Stansfield-only.
        cov_tuple = (0.0, 0.0, 0.0)
        method = _METHOD_STANSFIELD

    return (emitter_xy, method, cov_tuple)


def _fallback_centroid(
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
) -> tuple[float, float] | None:
    """Pairwise ray-ray crossings, weighted centroid -- the ADR-007 D3 path.

    For each pair ``(i, j)`` of bearings, compute the ray-ray crossing
    (if any), with combined weight ``w_i + w_j = 1/sigma_i^2 +
    1/sigma_j^2``. Take the weighted centroid of the crossings via
    ``geometry.weighted_centroid_of_crossings``. Return ``None`` if no
    pair yields a finite crossing (all rays parallel / anti-parallel).

    The fallback is honest about being a fallback: every consumer of
    the resulting ``FixEvent`` sees ``method = "fallback_centroid"``
    and ``confidence_level = LOW``.
    """
    crossings: list[tuple[float, float]] = []
    weights: list[float] = []

    n_bearings = len(bearings)
    for i in range(n_bearings):
        bi = bearings[i]
        ui = bearing_to_unit_vector(bi.azimuth_deg)
        wi = 1.0 / (bi.azimuth_sigma_deg * bi.azimuth_sigma_deg)
        pi = node_positions_enu[i]
        for j in range(i + 1, n_bearings):
            bj = bearings[j]
            uj = bearing_to_unit_vector(bj.azimuth_deg)
            wj = 1.0 / (bj.azimuth_sigma_deg * bj.azimuth_sigma_deg)
            pj = node_positions_enu[j]
            crossing = ray_ray_crossing(pi, ui, pj, uj)
            if crossing is None:
                continue
            crossings.append(crossing)
            weights.append(wi + wj)

    if not crossings:
        return None

    return weighted_centroid_of_crossings(crossings, weights)


def _ellipse_or_downgrade(
    covariance_tuple: tuple[float, float, float],
) -> EllipseENU | None:
    """Wrap the covariance -> ellipse conversion in a defensive ``None``-on-fail.

    The covariance from ``compute_covariance`` should already be PSD;
    this helper exists so the orchestrator does not have to inline a
    ``try`` block, and so the downgrade-to-Stansfield path stays
    readable. Returns ``None`` on any of:

    * Zero covariance (the ``_solve_emitter_position`` defensive
      sentinel after a ``SingularFisherInformationError``).
    * Non-PSD covariance (defensive -- should not reach this path).
    """
    sigma_xx, sigma_xy, sigma_yy = covariance_tuple
    # Zero-covariance sentinel from ``_solve_emitter_position``'s
    # defensive branch. We refuse to build an ellipse on a placeholder
    # covariance -- the caller substitutes the placeholder ellipse.
    if sigma_xx == 0.0 and sigma_yy == 0.0 and sigma_xy == 0.0:
        return None
    cov_2x2 = np.array(
        [[sigma_xx, sigma_xy], [sigma_xy, sigma_yy]],
        dtype=np.float64,
    )
    try:
        return covariance_to_ellipse(cov_2x2)
    except SingularFisherInformationError:
        return None


def _placeholder_ellipse(emitter_xy: tuple[float, float]) -> EllipseENU:
    """Return the LOW-confidence placeholder ellipse for the fallback path.

    Semi-axes = ``max(_FALLBACK_ELLIPSE_RANGE_FRACTION * |emitter_xy|,
    _MIN_FALLBACK_SEMI_M)`` -- a generous-but-finite circle that
    renders on the dashboard without claiming any covariance
    structure. The ``confidence_level = LOW`` label is the load-
    bearing signal that this is a degenerate fix; the ellipse is the
    visual placeholder ("we have a position, we do not have an
    honest uncertainty -- look at the LOW band").

    Orientation 0.0 (semi-major along East) is arbitrary -- a circle's
    orientation has no meaning. The contract validator requires
    ``semi_major_m >= semi_minor_m``; we satisfy this trivially with
    equal axes.
    """
    range_m = math.hypot(emitter_xy[0], emitter_xy[1])
    semi_m = max(_FALLBACK_ELLIPSE_RANGE_FRACTION * range_m, _MIN_FALLBACK_SEMI_M)
    return EllipseENU(
        semi_major_m=semi_m,
        semi_minor_m=semi_m,
        orientation_deg=0.0,
    )


def _consensus_emitter_class(
    bearings: Sequence[BearingReport],
) -> EmitterClass | None:
    """Majority-rule consensus across contributing bearings.

    Returns:

    * ``None`` if no contributing bearing classified (every
      ``emitter_class`` is ``None``).
    * A specific ``EmitterClass`` if a strict majority (> 50%) of
      classifying nodes agreed on that class.
    * ``EmitterClass.UNKNOWN`` if classifying nodes did not reach a
      strict majority (split decision).

    The classification confidence is not aggregated into the
    ``FixEvent`` in v1.0 (the contract has no
    ``classification_confidence`` field on ``FixEvent``); a future
    extension would track this alongside the consensus class.
    """
    classified = [b.emitter_class for b in bearings if b.emitter_class is not None]
    if not classified:
        return None

    counts = Counter(classified)
    most_common_class, most_common_count = counts.most_common(1)[0]
    # Strict majority: more than half of the *classifying* nodes agree.
    # A tied vote (e.g. 1-1-1) drops to UNKNOWN -- the system has
    # evidence the emitter was classified but cannot consensus-pick
    # one label.
    if most_common_count * 2 > len(classified):
        return most_common_class
    return EmitterClass.UNKNOWN


def _midpoint_t_unix_ns(bearings: Sequence[BearingReport]) -> int:
    """Arithmetic mean of contributing timestamps, in integer ns.

    Integer-only arithmetic to preserve nanosecond resolution end-to-
    end (HANDOFF / INTERFACES.md §0: float64 loses ns past ~2^53 ns).
    The result lands in the same half-open interval as the inputs and
    is strictly positive iff every input is strictly positive (the
    contract validator's invariant on ``t_unix_ns``).
    """
    total = sum(b.t_unix_ns for b in bearings)
    return total // len(bearings)


# The orchestration helpers above (_filter_to_time_window, etc.) are
# module-private and intentionally not exported.
__all__ = [
    "StansfieldMLEFuser",
]
