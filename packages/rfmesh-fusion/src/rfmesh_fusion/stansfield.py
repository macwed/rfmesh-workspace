"""Closed-form weighted-LS Stansfield seed.

The Stansfield estimator is the seed of the fusion pipeline. It turns a
batch of ``BearingReport`` s and their associated node positions
(already projected to a common ENU plane) into a single ``(east, north)``
emitter position in one 2x2 linear solve. The sprint-1 MLE step
(WS-CD-003) refines this seed; on degenerate geometries the ``Fuser``
catches ``DegenerateGeometryError`` and routes to the
``fallback_centroid`` path (ADR-007 D3).

CHOICE OF VARIANT
-----------------
Classical Stansfield with inverse-variance weights, per ADR-007 D1:

* Minimise ``Sum_i w_i * (n_i . (x - p_i))^2`` where ``n_i`` is the unit
  normal to bearing-i's ray (the bearing-line's perpendicular-distance
  metric) and ``w_i = 1 / sigma_i^2``.
* Closed-form 2x2 normal-equation solve, ``numpy.linalg.solve``.

Not chosen: Kaplan's range-iterated reweighting (the MLE step removes
finite-sample bias anyway, so the extra inner loop does not pay for
itself in sprint 1) and Don Ho's algebraic estimator (one more dependency
on a textbook variant for marginal benefit). The MLE step in WS-CD-003 is
what closes the bias gap; this module's job is to deliver a *consistent*
seed in the convex basin of the MLE objective, fast and deterministic.

UNIT-NORMAL CONVENTION
----------------------
For an azimuth ``theta`` (degrees, true north = 0, CW positive)::

    u_i = (sin theta, cos theta)  -- the bearing's unit direction
    n_i = (cos theta, -sin theta) -- u_i rotated 90 deg CW

Both ``+n_i`` and ``-n_i`` are valid line normals; the choice is
arbitrary because the residual ``n_i . (x - p_i)`` is squared. The
"rotate CW" convention is documented here so a future reader doing the
maths matches the code.

NUMERICAL HYGIENE
-----------------
All accumulation happens in float64 in the local ENU plane. The
constant ``(pi/180)^2`` factor that would convert ``sigma_deg^2`` to
``sigma_rad^2`` cancels exactly out of the normal equations (it scales
every weight equally), so it is omitted -- both sides of the LS
multiply by the same constant and the solution is identical. Keeping
``deg^2`` in the weights avoids one needless source of round-off and
makes the per-weight magnitudes more intuitive when debugging.

References
----------
* ``ADR-007-fusion-algorithm-choices.md`` D1, D3.
* ``ARCHITECTURE.md`` §6 (AoA cross-fix, no TDOA).
* ``INTERFACES.md`` §2 (``BearingReport`` semantics; honest
  ``azimuth_sigma_deg``).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from rfmesh_contracts.enums import (  # type: ignore[import-untyped, unused-ignore]
    Capability,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)

from .exceptions import DegenerateGeometryError
from .geometry import bearing_to_unit_vector

# Minimum number of bearings before a closed-form fix is even
# attempted. Two bearing lines define a unique crossing on
# non-degenerate geometry; one bearing is a ray, not a fix
# (``ARCHITECTURE.md`` §2 axis 3 -- the solver is indifferent to N >= 2).
_MIN_BEARINGS: int = 2

# Relative-rank threshold for the 2x2 normal matrix. The matrix is
# ``A = sum_i w_i * n_i n_i^T`` -- positive semi-definite by
# construction. For a 2x2 PSD matrix ``det(A) / trace(A)^2`` is roughly
# the ratio of the smaller eigenvalue to the larger, i.e. the inverse
# condition number. Below ``1e-12`` the system is effectively rank-1
# (all bearings parallel, or all nodes coincident with the same
# bearing). We catch this before ``numpy.linalg.solve`` and raise
# ``DegenerateGeometryError`` -- the ``Fuser`` then routes to
# ``fallback_centroid`` per ADR-007 D3.
_NEAR_SINGULAR_RATIO: float = 1e-12

# Pre-check threshold for "all node positions coincident". A bearings-
# only fix needs *some* baseline -- if every node is at the same point
# (within a nanometre, well below any survey precision), there is no
# parallax and the LS solution degenerates to the common node position
# regardless of which bearings were observed. The normal-matrix rank
# check does not catch this case (different bearings keep the matrix
# rank-2), so we screen geometrically before solving.
_COINCIDENT_NODES_THRESHOLD_M: float = 1e-9


def stansfield_seed(
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    """Closed-form weighted-LS Stansfield seed.

    Returns the emitter ``(east_m, north_m)`` position estimate in the
    same ENU frame as ``node_positions_enu``. Per-bearing weights are
    ``1 / azimuth_sigma_deg**2``; the constant ``deg^2 -> rad^2`` factor
    cancels out of the normal equations and is omitted for numerical
    hygiene.

    Parameters
    ----------
    bearings
        At least ``_MIN_BEARINGS`` (= 2) ``BearingReport`` objects, in
        the same order as ``node_positions_enu``. ``azimuth_sigma_deg``
        is a contract-validated strictly-positive field; we treat it as
        such and do not invent a fallback if it is ever ``0`` (Invariant
        4).
    node_positions_enu
        ENU coordinates of the nodes that produced the corresponding
        ``bearings``. Length must equal ``len(bearings)``. Caller has
        already projected from WGS-84 via ``projection.to_enu``.

    Returns
    -------
    tuple[float, float]
        ``(east_m, north_m)`` emitter estimate in the caller's ENU
        frame.

    Raises
    ------
    DegenerateGeometryError
        If ``len(bearings) < 2``; if ``len(bearings)`` and
        ``len(node_positions_enu)`` differ; if all rays are parallel,
        all nodes coincide with collinear bearings, or the 2x2 normal
        matrix is otherwise numerically singular
        (``det(A) / trace(A)^2 < 1e-12`` or
        ``numpy.linalg.LinAlgError`` from ``numpy.linalg.solve``).

    Algorithm
    ---------
    For each bearing ``i`` with azimuth ``theta_i`` and node position
    ``p_i = (p_x, p_y)`` in ENU:

    1. Unit direction ``u_i = (sin theta_i, cos theta_i)``.
    2. Unit normal     ``n_i = (cos theta_i, -sin theta_i)``.
    3. The signed perpendicular distance from a candidate emitter
       ``x = (x_e, x_n)`` to bearing-i's line is
       ``d_i = n_i . (x - p_i)``.
    4. Weight ``w_i = 1 / sigma_i^2`` (degrees-squared -- the constant
       ``(pi/180)^2`` cancels in the LS, see module docstring).
    5. The 2x2 normal-equation system is

       ::

           [ sum w_i n_x^2       sum w_i n_x n_y ] [x_e]
           [ sum w_i n_x n_y     sum w_i n_y^2   ] [x_n]
           =
           [ sum w_i n_x (n_x p_x + n_y p_y) ]
           [ sum w_i n_y (n_x p_x + n_y p_y) ]

    6. Solve with ``numpy.linalg.solve``.
    """
    n = len(bearings)
    if n < _MIN_BEARINGS:
        msg = f"stansfield_seed requires at least {_MIN_BEARINGS} bearings; got {n}."
        raise DegenerateGeometryError(msg)
    if len(node_positions_enu) != n:
        msg = (
            f"stansfield_seed: len(bearings)={n} does not match "
            f"len(node_positions_enu)={len(node_positions_enu)}."
        )
        raise DegenerateGeometryError(msg)
    # ADR-013 §G4 defense-in-depth (rf-dsp council NOTE on 53140df):
    # Fuser.fuse() filters L1_REFUSED_PROMINENCE reports out before
    # calling stansfield_seed. If a future caller bypasses fuse() and
    # passes a refusal report directly, the sentinel
    # azimuth_sigma_deg=180.0 would still produce a (very low-weight)
    # contribution rather than the correct loud refusal. Catch the
    # bypass here.
    for i, bearing in enumerate(bearings):
        if bearing.method is Capability.L1_REFUSED_PROMINENCE:
            msg = (
                f"stansfield_seed: bearings[{i}] is a L1_REFUSED_PROMINENCE "
                "refusal event; the caller (typically Fuser.fuse) must filter "
                "these out before invoking the solver. Including a sentinel "
                "report poisons the inverse-variance weight."
            )
            raise DegenerateGeometryError(msg)

    # Geometric pre-check: at least one pair of nodes must have some
    # baseline. If every node is at (essentially) the same point, the
    # normal matrix would still be rank-2 (as long as the bearings
    # differ) and ``numpy.linalg.solve`` would happily return that
    # common node position as the "emitter" -- a mathematically
    # consistent but geometrically meaningless answer. We refuse and
    # let the fuser take the fallback path.
    east_coords = [p[0] for p in node_positions_enu]
    north_coords = [p[1] for p in node_positions_enu]
    east_span = max(east_coords) - min(east_coords)
    north_span = max(north_coords) - min(north_coords)
    if east_span < _COINCIDENT_NODES_THRESHOLD_M and north_span < _COINCIDENT_NODES_THRESHOLD_M:
        msg = (
            "stansfield_seed: all node positions are coincident "
            f"(east span {east_span:.3e} m, north span {north_span:.3e} m); "
            "geometry is rank-deficient -- no parallax baseline."
        )
        raise DegenerateGeometryError(msg)

    # Build the normal matrix A and right-hand side b in float64.
    # A is symmetric 2x2; we accumulate a00, a01, a11 separately and
    # mirror at the end. b is a length-2 vector.
    a00 = 0.0
    a01 = 0.0
    a11 = 0.0
    b0 = 0.0
    b1 = 0.0

    for bearing, (p_x, p_y) in zip(bearings, node_positions_enu, strict=True):
        sigma_deg = bearing.azimuth_sigma_deg
        # The contract validator enforces ``sigma_deg > 0``. Trusting
        # that here -- a defensive ``if sigma_deg <= 0: raise`` would
        # paper over a broken contract rather than catch one.
        weight = 1.0 / (sigma_deg * sigma_deg)

        u_x, u_y = bearing_to_unit_vector(bearing.azimuth_deg)
        # Unit normal: rotate the unit direction 90 deg clockwise in
        # the ENU plane. ``(u_x, u_y) -> (u_y, -u_x)``.
        n_x = u_y
        n_y = -u_x

        # Inner product ``n_i . p_i``; the LS RHS is ``sum w * n * (n . p)``.
        n_dot_p = n_x * p_x + n_y * p_y

        a00 += weight * n_x * n_x
        a01 += weight * n_x * n_y
        a11 += weight * n_y * n_y
        b0 += weight * n_x * n_dot_p
        b1 += weight * n_y * n_dot_p

    # Rank check before handing to numpy. ``det(A) / trace(A)^2`` is
    # roughly the inverse condition number for a 2x2 PSD matrix; below
    # the threshold we are effectively rank-1 (all bearings parallel,
    # or all nodes coincident with collinear bearings). We catch this
    # ourselves rather than letting numpy's tolerance decide, so the
    # message stays informative.
    trace = a00 + a11
    det = a00 * a11 - a01 * a01
    if trace <= 0.0 or det <= _NEAR_SINGULAR_RATIO * (trace * trace):
        msg = (
            "stansfield_seed: normal matrix is rank-deficient / singular / "
            f"degenerate (trace={trace:.6e}, det={det:.6e}); all rays may be "
            "parallel or all nodes coincident."
        )
        raise DegenerateGeometryError(msg)

    matrix_a = np.array([[a00, a01], [a01, a11]], dtype=np.float64)
    rhs_b = np.array([b0, b1], dtype=np.float64)

    try:
        solution = np.linalg.solve(matrix_a, rhs_b)
    except np.linalg.LinAlgError as exc:
        # Numpy decided the system is singular even though our
        # pre-check passed. Re-raise as the domain exception so the
        # fuser can route to the fallback path uniformly.
        msg = f"stansfield_seed: numpy.linalg.solve reported singular matrix: {exc}"
        raise DegenerateGeometryError(msg) from exc

    return (float(solution[0]), float(solution[1]))
