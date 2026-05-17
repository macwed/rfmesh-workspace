"""Post-fit per-node residuals + ``is_outlier`` flags.

For a solved emitter position ``(x_e, x_n)`` in ENU metres and the batch
of ``BearingReport`` objects + ENU node positions that produced it, this
module computes -- for each contributing bearing -- the signed angular
residual

::

    r_i = wrap_to_pi_deg(measured_azimuth_deg_i - predicted_azimuth_deg_i)

and the per-bearing outlier flag (binding per ADR-005 §D3)

::

    is_outlier_i = (|r_i| / sigma_i) > 3

where ``sigma_i`` is that bearing's own ``BearingReport.azimuth_sigma_deg``
-- not a global sigma, not a re-derived empirical sigma. The strict ``>``
matches ADR-005 §D3 exactly.

This is the self-diagnosis channel the demo shows on the dashboard. A
node whose residual is many sigmas off its own reported uncertainty is
the *probable multipath / calibration outlier* per HANDOFF §0 Advantage
#6; ADR-005 §D3 + ADR-009 then downgrade the fix from HIGH to MEDIUM in
``confidence.py``. The downgrade is on *display*, not on inclusion in
the solver: per ADR-007 D3 and HANDOFF §3, sprint-1 is
honesty-over-robustness and the solver does not reject the outlier --
the dashboard shows it.

The ordering of ``ResidualsResult.residuals_deg`` and
``.is_outlier`` matches the input ``bearings`` order, which matches
``contributing_node_ids``, which is the ordering anchor for
``FixEvent.contributing_nodes`` and ``FixEvent.residuals_deg`` per
``INTERFACES.md`` §3.

CONVENTION ALIGNMENT WITH ``mle.py``
------------------------------------
The predicted azimuth uses the *forward* (node -> emitter) direction
with east-offset first, north-offset second::

    theta_pred_rad = atan2(x_e - p_e, x_n - p_n)

algebraically equal to ``mle.py``'s ``atan2(-delta_e, -delta_n)`` with
``delta_e = p_e - x_e`` and ``delta_n = p_n - x_n``. The residual sign
convention is ``measured - predicted``, matching ``mle.py``'s
``_wrap_to_pi(theta_meas - theta_pred)``. Both choices are load-bearing:
WS-CD-007's fuser uses both MLE's internal residuals (for Gauss-Newton
convergence) and this module's post-fit residuals (for the
``FixEvent.residuals_deg`` payload); a disagreement would have the
dashboard and the solver tell different stories about the same fix.

References
----------
* ``docs/adr/ADR-005-fusion-confidence-policy.md`` §D3 (the
  ``is_outlier_i = (|r_i| / sigma_i > 3)`` definition; strictly
  greater).
* ``docs/adr/ADR-009-confidence-band-math-correction-and-demo-narrative.md``
  (outlier flag downgrades HIGH -> MEDIUM in confidence policy).
* ``INTERFACES.md`` §3 -- ``FixEvent.residuals_deg`` is in
  ``contributing_nodes`` order; both lengths equal.
* ``ARCHITECTURE.md`` §6 (AoA cross-fix; bearings are geographic; node
  runtime applies heading correction upstream).
* ``HANDOFF_TO_CLAUDE_CODE_LEAD.md`` §0 Advantage #6 (honesty payload,
  residuals + outlier flag = self-diagnosis); §2 B3 (no silent
  fallbacks); §2 B4 (demo honesty payload); §2 B5 (pure fusion).
* ``packages/rfmesh-fusion/src/rfmesh_fusion/mle.py`` module docstring
  -- the azimuth convention and residual sign are inherited from it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)

from .exceptions import FusionError

# The ADR-005 §D3 outlier threshold: ``|r_i| / sigma_i > 3``. Pinned
# as a module constant so a single locus carries the value the
# confidence policy and the dashboard both reference. Strictly ``>``;
# a residual of exactly ``3 * sigma`` is NOT an outlier
# (``test_is_outlier_threshold_at_exactly_3`` locks this).
_OUTLIER_SIGMA_MULTIPLIER: float = 3.0


@dataclass(frozen=True, slots=True)
class ResidualsResult:
    """Per-bearing post-fit residuals + outlier flags.

    Attributes
    ----------
    residuals_deg
        Signed angular residual per bearing, in degrees, in the
        shortest-arc representative ``[-180, +180)`` (closed at
        ``-180``, open at ``+180``; see ``_wrap_to_signed_deg`` for
        the boundary-case rationale). Indexed parallel to
        ``contributing_node_ids`` and to the input ``bearings``
        sequence. Sign convention: ``measured azimuth - predicted
        azimuth`` at the solved emitter position, matching
        ``mle.py``'s internal residual convention exactly.
    is_outlier
        Per-bearing flag: ``True`` iff ``|r_i| / sigma_i > 3``
        (strictly greater, per ADR-005 §D3). Consumed by
        ``confidence.py`` to downgrade ``HIGH`` -> ``MEDIUM``
        (ADR-009). The flag is per *bearing*, not per *node*: if a
        single physical node ever contributed two bearings to the
        same batch (the runtime currently does not, but the contract
        does not forbid it), each is flagged independently.
    contributing_node_ids
        ``node_id`` of each contributing bearing, in input order. The
        ordering anchor for ``FixEvent.contributing_nodes`` and
        ``FixEvent.residuals_deg`` per ``INTERFACES.md`` §3 -- the
        WS-CD-007 fuser plugs all three of these into the
        ``FixEvent`` it constructs, and the order must be preserved
        end-to-end.
    """

    residuals_deg: tuple[float, ...]
    is_outlier: tuple[bool, ...]
    contributing_node_ids: tuple[str, ...]


def _predicted_azimuth_deg(
    solved_emitter_xy_enu: tuple[float, float],
    node_xy_enu: tuple[float, float],
) -> float:
    """Geographic azimuth from ``node_xy_enu`` to ``solved_emitter_xy_enu``.

    Degrees, true north = 0, clockwise positive, range ``[0, 360)``.
    Forward (node -> emitter) direction; east-offset first, north-offset
    second -- matching ``geometry.bearing_to_unit_vector``'s
    ``(east, north) = (sin, cos)`` convention and ``mle.py``'s
    ``_predicted_azimuths_and_jacobian``. The ``% 360.0`` puts the
    result in the same ``[0, 360)`` range the contract validator
    enforces on ``BearingReport.azimuth_deg``, so the subsequent
    measured-minus-predicted difference is between two
    ``[0, 360)`` values and the wrap-to-(-180, +180] handles the seam.
    """
    x_e, x_n = solved_emitter_xy_enu
    p_e, p_n = node_xy_enu
    delta_e = x_e - p_e
    delta_n = x_n - p_n
    az_deg = math.degrees(math.atan2(delta_e, delta_n))
    return az_deg % 360.0


def _wrap_to_signed_deg(diff_deg: float) -> float:
    """Wrap a degree difference to the shortest-arc ``[-180, +180)`` arc.

    Uses the modulo form ``((diff + 180) % 360) - 180``, which is
    exact for any finite double-precision input thanks to Python's
    ``%`` operator returning a non-negative remainder for a positive
    divisor. Compared to the ``atan2(sin, cos)`` double-arctangent
    form (used by ``mle.py`` because numpy's modulo is less reliable
    on arrays), the pure-Python modulo avoids the ~5e-16 deg trig
    round-off that would otherwise push a residual of *exactly*
    ``3 * sigma`` over the strict ``|r| / sigma > 3`` outlier
    threshold (ADR-005 §D3) by a sub-machine-epsilon margin. The two
    forms agree to within float64 round-off everywhere except at the
    threshold, where the modulo form is the honest answer.

    Convention. The output lies in ``[-180, +180)`` -- closed at
    ``-180``, open at ``+180``. A diff of exactly ``+180.0`` is
    mapped to ``-180.0`` (the antipodal point is ambiguous; the
    modulo arithmetic picks the negative representative). A diff of
    exactly ``-180.0`` stays ``-180.0``. No production geometry
    lands on the boundary (an emitter at the antipode of every node
    is geometrically impossible), but the choice is documented so a
    future reader sees what edge case the arithmetic resolves.

    Why pure Python rather than numpy. The arithmetic is on a single
    scalar per bearing; numpy adds an allocation cost that dominates
    the inner add/mod/sub for a 3-bearing fix, and the ``%`` operator
    on numpy ``float64`` scalars has been historically less robust
    than Python's at the boundary. ``math`` is the right tool here.
    """
    return ((diff_deg + 180.0) % 360.0) - 180.0


def compute_residuals(
    solved_emitter_xy_enu: tuple[float, float],
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
) -> ResidualsResult:
    """Post-fit residuals + outlier flags for one fix.

    Parameters
    ----------
    solved_emitter_xy_enu
        Solved emitter position ``(east_m, north_m)`` in the same ENU
        frame as ``node_positions_enu``. Typically the converged MLE
        position from ``solve_mle`` (or the Stansfield seed when MLE
        was skipped, or the ``fallback_centroid`` per ADR-007 D3).
    bearings
        The ``BearingReport`` objects that contributed to the fix, in
        the order ``FixEvent.contributing_nodes`` will preserve.
    node_positions_enu
        ENU coordinates of the corresponding nodes -- the *same*
        positions the solver used. Length must equal ``len(bearings)``;
        order must match.

    Returns
    -------
    ResidualsResult
        Per-bearing signed residual (deg, shortest-arc in
        ``(-180, +180]``), per-bearing outlier flag (``|r| / sigma >
        3`` strictly), and the ordering-anchor tuple of ``node_id``
        values.

    Raises
    ------
    FusionError
        If ``len(bearings) != len(node_positions_enu)`` (structural
        input error); if any bearing has ``azimuth_sigma_deg <= 0``
        (defence-in-depth -- the contract validator already rejects
        this upstream, but Invariant B3 says we never silently
        divide by zero).
    """
    n = len(bearings)
    if len(node_positions_enu) != n:
        msg = (
            f"compute_residuals: len(bearings)={n} does not match "
            f"len(node_positions_enu)={len(node_positions_enu)}."
        )
        raise FusionError(msg)

    residuals_deg: list[float] = []
    is_outlier: list[bool] = []
    contributing_node_ids: list[str] = []

    for bearing, node_xy in zip(bearings, node_positions_enu, strict=True):
        sigma_deg = bearing.azimuth_sigma_deg
        if sigma_deg <= 0.0:
            # Defence-in-depth (Invariant B3). The contract validator
            # enforces ``azimuth_sigma_deg > 0``; if a malformed bearing
            # ever reaches this code (e.g. via ``model_construct``
            # bypassing validators, or a test shim), refuse loudly
            # rather than emit a ``+inf`` outlier ratio that would
            # downgrade every fix touching that bearing silently.
            msg = (
                f"compute_residuals: bearing for node_id="
                f"{bearing.node_id!r} has non-positive "
                f"azimuth_sigma_deg={sigma_deg!r}; refusing to divide "
                "by zero (Invariant B3)."
            )
            raise FusionError(msg)

        predicted_deg = _predicted_azimuth_deg(solved_emitter_xy_enu, node_xy)
        # Sign convention: measured - predicted, matching mle.py's
        # ``residual = _wrap_to_pi(theta_meas - theta_pred)``. The
        # wrap maps the raw difference (which can be anywhere in
        # roughly ``(-360, +360)``) to the shortest-arc
        # representative.
        raw_diff_deg = bearing.azimuth_deg - predicted_deg
        residual_deg = _wrap_to_signed_deg(raw_diff_deg)

        # Outlier test: ADR-005 §D3, strictly ``>``. Absolute value
        # comparison -- the *magnitude* of the residual is what
        # matters; sign is preserved in ``residuals_deg`` for the
        # operator's "which way did the bearing point off-truth"
        # diagnostic.
        is_outlier_flag = abs(residual_deg) / sigma_deg > _OUTLIER_SIGMA_MULTIPLIER

        residuals_deg.append(residual_deg)
        is_outlier.append(is_outlier_flag)
        contributing_node_ids.append(bearing.node_id)

    return ResidualsResult(
        residuals_deg=tuple(residuals_deg),
        is_outlier=tuple(is_outlier),
        contributing_node_ids=tuple(contributing_node_ids),
    )
