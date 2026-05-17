"""Gauss-Newton MLE refinement of the Stansfield seed.

The Stansfield estimator (``stansfield.py``) is a *consistent* but
finite-sample-biased seed: it minimises the sum of squared
perpendicular distances to the bearing lines, which is *not* the
maximum-likelihood objective for Gaussian angular noise. MLE on
azimuth measurements minimises the sum of squared angular residuals
weighted by ``1/sigma_i^2``, which is what ``solve_mle`` here does.

The two-step "Stansfield seed -> Gauss-Newton refinement" pattern is
the project's settled choice per ``ADR-007-fusion-algorithm-choices.md``
D2:

* Closed-form Stansfield places the seed inside the convex basin of
  the MLE objective for non-degenerate geometries (D3 catches
  rank-deficiency ab initio).
* Gauss-Newton with the analytic Jacobian iterates to the true MLE,
  closing the finite-sample bias gap. ``||Δx|| < 1e-3 m`` and at most
  50 iterations are the convergence criteria from D2.
* The dashboard's "Stansfield vs Stansfield+MLE" side-by-side panel
  (ADR-007 §Neutral) needs both outputs visible -- which is why this
  module refines a *separately-supplied* seed rather than calling
  ``stansfield_seed`` internally.

ALGORITHM (compact form)
------------------------
Let ``x = (x_e, x_n)`` be the candidate emitter position, ``p_i =
(p_x_i, p_y_i)`` the node ``i`` position, both in ENU metres, and
``theta_i_meas`` the measured azimuth (radians, true north = 0, CW
positive). ``BearingReport.azimuth_deg`` reports the *node-to-
emitter* direction (the direction of arrival the node observed) --
see ``test_stansfield.py``'s ``_azimuth_node_to_emitter`` helper
and ``geometry.bearing_to_unit_vector``'s ``(east, north) =
(sin(az), cos(az))`` mapping with the unit vector understood as
pointing along the bearing the node reports. The predicted azimuth
from node ``i`` to the candidate emitter is therefore the *forward*
direction (east-offset of emitter from node, first; north-offset
second):

::

    theta_pred_i = atan2(x_e - p_x_i, x_n - p_y_i)

This is the corrected convention vs the ticket's compact derivation
note, which had the offset order reversed (``atan2(p - x)`` gives
the back-azimuth, off by π from the node's reported bearing). The
Jacobian rows are unaffected by the π-offset: ``atan2(a, b)`` and
``atan2(-a, -b)`` differ by a constant and the constant drops out
of the derivative. With offsets ``de_i = x_e - p_x_i`` and ``dn_i =
x_n - p_y_i`` and ``r_i² = de_i² + dn_i²``, the rows read:

::

    J[i, :] = [  dn_i / r_i² ,  -de_i / r_i² ]
            = [  (x_n - p_y_i) / r_i² ,  -(x_e - p_x_i) / r_i² ]

Equivalently, with ``Δe = p_x - x_e`` and ``Δn = p_y - x_n`` (the
ticket's notation; ``de = -Δe``, ``dn = -Δn``):

::

    J[i, :] = [ -Δn_i / r_i² ,  Δe_i / r_i² ]
            = [ -(p_y_i - x_n) / r_i² ,  (p_x_i - x_e) / r_i² ]

-- matching the ticket's published Jacobian exactly. The angular
residual (wrapped to ``[-pi, pi]``):

::

    r_i = wrap(theta_meas_i - theta_pred_i)

The weight matrix:

::

    W = diag(1 / sigma_i_rad²)               # sigma converted to rad ONCE.

(See implementation notes in the ticket and the docstring of
``_predicted_azimuths_and_jacobian`` for the chain-rule derivation.)
The Gauss-Newton update is the 2x2 solve

::

    (J^T W J + damping * I_2)  Δx  =  J^T W r
    x_new = x + Δx

with ``numpy.linalg.solve``; ``damping = 0.0`` is pure Gauss-Newton
(the ADR-007 D2 default). Convergence: ``||Δx|| < tol_m``. Divergence:
``||x_new - x_seed|| > divergence_radius_m`` at any iterate, or
``numpy.linalg.solve`` raising ``LinAlgError`` -- in either case a
``MLEConvergenceError`` is raised (Invariant B3, no silent
fallbacks).

WHY THE JACOBIAN ARG ORDER IS ``atan2(east, north)`` -- not ``atan2(y, x)``
---------------------------------------------------------------------------
The project's azimuth convention (`INTERFACES.md` §0 and
`geometry.bearing_to_unit_vector`) is true north = 0, CW positive,
i.e. ``east = sin(theta)``, ``north = cos(theta)``. The inverse map
(unit vector -> azimuth) is therefore ``atan2(east_component,
north_component)`` -- the *east-offset first*, *north-offset second*
form, **not** the maths-textbook ``atan2(y, x)`` with ``y = north``.
Get this wrong and the analytic Jacobian's signs flip, the test
``test_mle_analytic_jacobian_matches_finite_differences`` catches
it, and a long debug session ensues.

REFERENCES
----------
* ``ADR-007-fusion-algorithm-choices.md`` D2 (Gauss-Newton, analytic
  Jacobian, 1e-3 m tolerance, 50-iter cap).
* ``INTERFACES.md`` §0 (azimuth convention) and §3 (BearingReport
  semantics; honest ``azimuth_sigma_deg`` -> honest inverse-variance
  weights).
* ``ARCHITECTURE.md`` §6 (AoA cross-fix, not TDOA).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)

from .exceptions import MLEConvergenceError

# Minimum number of bearings before MLE is even attempted. The
# Stansfield seed enforces the same floor; this is a defensive
# duplicate so ``solve_mle`` is correct in isolation (callers other
# than the Stansfield+MLE composite may exist).
_MIN_BEARINGS: int = 2

# Default convergence and divergence thresholds. Pinned by
# ADR-007 D2 (``||Δx|| < 1e-3 m``, max 50 iterations) and by the
# matching D3 threshold of 50 km from the seed/centroid. Keeping the
# two thresholds aligned makes the operator-facing failure stories
# read the same way regardless of which path raised.
_DEFAULT_TOL_M: float = 1e-3
_DEFAULT_MAX_ITER: int = 50
_DEFAULT_DIVERGENCE_RADIUS_M: float = 50_000.0


@dataclass(frozen=True, slots=True)
class MLEResult:
    """Refined emitter position from Gauss-Newton MLE.

    Attributes
    ----------
    position
        ``(east_m, north_m)`` in the same ENU frame as the seed and
        node positions.
    n_iter
        Number of Gauss-Newton iterations actually run (``>= 1`` on
        success; equal to ``max_iter`` only on the divergence-by-
        budget path, which raises before returning).
    converged
        ``True`` iff the final ``||Δx|| < tol_m`` and the trajectory
        stayed inside the divergence radius. On any other terminal
        condition the function raises ``MLEConvergenceError`` rather
        than returning ``converged = False`` -- the flag is on the
        result type for symmetry with future variants (a future LM
        variant may want to return a non-converged-but-not-divergent
        result with a confidence downgrade rather than raising), not
        to label silent failures.
    """

    position: tuple[float, float]
    n_iter: int
    converged: bool


def _measured_azimuths_rad(bearings: Sequence[BearingReport]) -> np.ndarray:
    """Return the measured azimuths in radians as a 1-D float64 array.

    ``BearingReport.azimuth_deg`` is contract-validated to ``[0,
    360)``; we convert to radians once up-front so the iteration loop
    only sees radians.
    """
    return np.array(
        [math.radians(b.azimuth_deg) for b in bearings],
        dtype=np.float64,
    )


def _inverse_variance_weights_rad(bearings: Sequence[BearingReport]) -> np.ndarray:
    """Return ``1 / sigma_rad^2`` for each bearing as a 1-D float64 array.

    The contract validator enforces ``azimuth_sigma_deg > 0``
    (Invariant B3 -- no defensive ``if sigma <= 0`` fallback; an
    invalid sigma is a broken contract upstream and we trust the
    validator). Conversion from degrees-squared to radians-squared is
    done ONCE here, outside the iteration loop.
    """
    sigma_deg = np.array(
        [b.azimuth_sigma_deg for b in bearings],
        dtype=np.float64,
    )
    sigma_rad = np.radians(sigma_deg)
    return 1.0 / (sigma_rad * sigma_rad)


def _wrap_to_pi(angle_rad: np.ndarray) -> np.ndarray:
    """Wrap angles to ``[-pi, pi]`` via ``atan2(sin, cos)``.

    The naive ``(x + pi) % (2 pi) - pi`` form fails for inputs slightly
    outside ``[-pi, pi]`` due to round-off at the boundary (the modulo
    can return ``2 pi`` instead of ``0``). The ``atan2`` form is robust
    to any finite real input.
    """
    # ``np.arctan2`` is typed ``Any`` in numpy's stubs; cast to make
    # the returned ndarray type explicit for downstream callers.
    wrapped: np.ndarray = np.arctan2(np.sin(angle_rad), np.cos(angle_rad))
    return wrapped


def _predicted_azimuths_and_jacobian(
    x_emitter: tuple[float, float],
    node_positions_enu: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return predicted azimuths (radians) and the analytic Jacobian.

    Parameters
    ----------
    x_emitter
        Candidate emitter position ``(x_e, x_n)`` in ENU metres.
    node_positions_enu
        ``(n, 2)`` float64 array of node positions in ENU metres,
        columns ``(east, north)``.

    Returns
    -------
    theta_pred : np.ndarray
        Length-``n`` predicted azimuths in radians, computed as
        ``atan2(x_e - p_e, x_n - p_n)`` -- the *forward* (node-to-
        emitter) direction, east-offset first, matching the bearing
        ``BearingReport.azimuth_deg`` actually reports. The Stansfield
        test helper ``_azimuth_node_to_emitter`` uses the same form,
        so the two paths agree by construction.
    jacobian : np.ndarray
        ``(n, 2)`` float64 array of partial derivatives
        ``d theta_pred / d (x_e, x_n)``. With offsets ``Δe = p_e -
        x_e``, ``Δn = p_n - x_n``, ``r^2 = Δe^2 + Δn^2``, each row is
        ``[-Δn / r^2, Δe / r^2]`` (radians per metre).

        Derivation. ``theta_pred = atan2(x_e - p_e, x_n - p_n) =
        atan2(-Δe, -Δn)``. ``atan2(-a, -b)`` differs from
        ``atan2(a, b)`` by a constant ``±pi``, so the partials are
        the same as for ``atan2(Δe, Δn)``: ``∂/∂a = b/(a^2+b^2)``,
        ``∂/∂b = -a/(a^2+b^2)``, applied with chain rule
        ``∂Δe/∂x_e = -1`` and ``∂Δn/∂x_n = -1``, gives
        ``∂theta/∂x_e = -Δn / r^2``, ``∂theta/∂x_n = Δe / r^2``.
        The ticket's published Jacobian formula -- this one -- is
        therefore correct regardless of which way the predicted
        azimuth points; only the residual computation is affected by
        the direction choice.

    Raises
    ------
    MLEConvergenceError
        If any ``r_i^2`` is exactly zero -- the emitter iterate has
        landed on top of a node, the Jacobian is singular at that
        bearing, and we refuse to invent a derivative (Invariant B3).
    """
    x_e, x_n = x_emitter
    # ``Δe`` and ``Δn`` in the ticket's notation (node-minus-emitter).
    # The Jacobian uses these; the predicted azimuth uses their
    # negation (emitter-minus-node), which matches the direction
    # ``BearingReport.azimuth_deg`` reports (node -> emitter).
    delta_e = node_positions_enu[:, 0] - x_e
    delta_n = node_positions_enu[:, 1] - x_n
    r_squared = delta_e * delta_e + delta_n * delta_n

    if not np.all(r_squared > 0.0):
        msg = (
            "solve_mle: Gauss-Newton iterate landed on top of a node "
            f"(emitter at {x_emitter}); Jacobian is singular at that "
            "bearing -- refusing to continue (Invariant B3)."
        )
        raise MLEConvergenceError(msg)

    # Predicted azimuth: forward direction (node -> emitter), east
    # offset first, north second. The ``-delta_e``/``-delta_n``
    # arguments give the emitter-minus-node offsets, i.e. the
    # direction the node would point to see the emitter -- matching
    # ``test_stansfield.py``'s ``_azimuth_node_to_emitter`` helper
    # and ``geometry.bearing_to_unit_vector``'s ``(east, north) =
    # (sin(az), cos(az))`` convention.
    theta_pred = np.arctan2(-delta_e, -delta_n)

    # Jacobian columns: d theta / d x_e = -Δn / r^2,
    # d theta / d x_n = Δe / r^2. The pi-offset between the two
    # predicted-azimuth conventions is a constant and drops out.
    jacobian = np.empty((node_positions_enu.shape[0], 2), dtype=np.float64)
    jacobian[:, 0] = -delta_n / r_squared
    jacobian[:, 1] = delta_e / r_squared
    return theta_pred, jacobian


def solve_mle(
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
    seed_xy: tuple[float, float],
    *,
    tol_m: float = _DEFAULT_TOL_M,
    max_iter: int = _DEFAULT_MAX_ITER,
    divergence_radius_m: float = _DEFAULT_DIVERGENCE_RADIUS_M,
    damping: float = 0.0,
) -> MLEResult:
    """Refine the Stansfield seed via Gauss-Newton with analytic Jacobian.

    Iterates ``(J^T W J + damping * I_2) Δx = J^T W r`` from
    ``seed_xy`` until either ``||Δx|| < tol_m`` (success) or one of
    the failure conditions below holds (raises). The weight matrix
    ``W = diag(1 / sigma_i_rad^2)`` uses ``BearingReport.
    azimuth_sigma_deg`` converted to radians once before the loop.
    The angular residual is wrapped to ``[-pi, pi]`` every iteration
    so an emitter near the 0/360-deg azimuth seam does not produce a
    ~2 pi spurious residual.

    Parameters
    ----------
    bearings
        ``>= 2`` ``BearingReport`` objects, in the same order as
        ``node_positions_enu``.
    node_positions_enu
        ENU coordinates of the corresponding nodes. Length must equal
        ``len(bearings)``. Caller has already projected from WGS-84
        via ``projection.to_enu``.
    seed_xy
        Initial guess ``(east_m, north_m)``. Typically the output of
        ``stansfield_seed``; ``solve_mle`` is agnostic to the
        provenance.
    tol_m
        Convergence threshold on ``||Δx||`` in metres. Default
        ``1e-3`` (ADR-007 D2).
    max_iter
        Iteration budget. Default ``50`` (ADR-007 D2).
    divergence_radius_m
        Distance from ``seed_xy`` at which an iterate is declared
        divergent. Default ``50_000`` m (ADR-007 D3 alignment).
    damping
        Levenberg-Marquardt-style diagonal addition to ``J^T W J``.
        Default ``0.0`` is pure Gauss-Newton (the ADR-007 D2 choice).
        A positive value adds ``damping * I_2`` before solving the
        2x2 normal equations; the parameter is plumbed for a future
        opt-in if real-data geometries surface a convergence issue
        (ADR-007 §Negative consequences). Must be non-negative.

    Returns
    -------
    MLEResult
        ``position`` is the refined ``(east_m, north_m)`` in the same
        ENU frame as the inputs; ``n_iter`` is the iteration count
        (``>= 1``); ``converged`` is always ``True`` on this path --
        every non-converged terminal condition raises.

    Raises
    ------
    MLEConvergenceError
        If ``len(bearings) < 2`` or lengths do not match;
        if ``max_iter`` iterations elapse without ``||Δx|| < tol_m``;
        if the iterate drifts more than ``divergence_radius_m`` from
        ``seed_xy``; if the Jacobian collapses (iterate on top of a
        node, or ``numpy.linalg.solve`` reports a singular update
        system).
    ValueError
        If ``tol_m`` or ``max_iter`` are not positive, or ``damping``
        is negative -- structurally-invalid inputs distinct from a
        legitimately-divergent solve.
    """
    if tol_m <= 0.0:
        msg = f"solve_mle: tol_m must be positive; got {tol_m}."
        raise ValueError(msg)
    if max_iter <= 0:
        msg = f"solve_mle: max_iter must be positive; got {max_iter}."
        raise ValueError(msg)
    if damping < 0.0:
        msg = f"solve_mle: damping must be non-negative; got {damping}."
        raise ValueError(msg)

    n = len(bearings)
    if n < _MIN_BEARINGS:
        msg = (
            f"solve_mle: requires at least {_MIN_BEARINGS} bearings; "
            f"got {n}. (Distinct from DegenerateGeometryError -- that "
            "is the closed-form path's rank-deficiency signal; this "
            "is the iterative path's structural-input refusal.)"
        )
        raise MLEConvergenceError(msg)
    if len(node_positions_enu) != n:
        msg = (
            f"solve_mle: len(bearings)={n} does not match "
            f"len(node_positions_enu)={len(node_positions_enu)}."
        )
        raise MLEConvergenceError(msg)

    # Pre-compute everything that does not change across iterations:
    # measured azimuths (rad), inverse-variance weights (1/rad^2), and
    # the node positions as a (n, 2) float64 array.
    theta_meas = _measured_azimuths_rad(bearings)
    weights = _inverse_variance_weights_rad(bearings)
    nodes_array = np.array(node_positions_enu, dtype=np.float64)

    seed_array = np.array(seed_xy, dtype=np.float64)
    x_current = seed_array.copy()

    for iter_idx in range(1, max_iter + 1):
        theta_pred, jacobian = _predicted_azimuths_and_jacobian(
            (float(x_current[0]), float(x_current[1])),
            nodes_array,
        )

        # Angular residual, wrapped to [-pi, pi]. This is the
        # measured-minus-predicted convention; the sign convention is
        # absorbed into the Gauss-Newton update either way (both
        # sides flip with the sign), but we standardise here so the
        # debug-trace residuals read in the expected direction.
        residual = _wrap_to_pi(theta_meas - theta_pred)

        # Weighted normal equations. J is (n, 2), W is a 1-D weight
        # vector of length n. ``(J.T * w) @ J`` is equivalent to
        # ``J.T @ diag(w) @ J`` but avoids forming the diagonal matrix
        # explicitly; ``(J.T * w) @ r`` likewise.
        jtw = jacobian.T * weights
        normal_matrix = jtw @ jacobian  # shape (2, 2)
        rhs = jtw @ residual  # shape (2,)

        if damping > 0.0:
            # Levenberg-Marquardt-style additive damping on the
            # diagonal. ``damping = 0.0`` (the default) makes this a
            # no-op and the solve is pure Gauss-Newton, byte-for-
            # byte equal to skipping the branch.
            normal_matrix = normal_matrix + damping * np.eye(2, dtype=np.float64)

        try:
            step = np.linalg.solve(normal_matrix, rhs)
        except np.linalg.LinAlgError as exc:
            msg = (
                f"solve_mle: did not converge -- numpy.linalg.solve "
                f"reported a singular update system at iteration "
                f"{iter_idx} (J^T W J collapsed to rank-1; the "
                "bearings effectively went parallel at the current "
                f"iterate). Last position: ({float(x_current[0]):.6f}, "
                f"{float(x_current[1]):.6f})."
            )
            raise MLEConvergenceError(msg) from exc

        x_new = x_current + step

        # Divergence check: distance from the seed, not from the
        # previous iterate. The seed is the Stansfield closed-form
        # answer and a Gauss-Newton refinement should never wander
        # that far from it on a non-degenerate geometry. 50 km
        # matches ADR-007 D3's divergence threshold against the node
        # centroid -- two paths, one number, one mental model.
        drift_m = float(np.linalg.norm(x_new - seed_array))
        if drift_m > divergence_radius_m:
            msg = (
                f"solve_mle: did not converge -- iterate diverged "
                f"{drift_m:.1f} m from seed (limit "
                f"{divergence_radius_m:.0f} m) at iteration "
                f"{iter_idx}. Last position: ({float(x_new[0]):.6f}, "
                f"{float(x_new[1]):.6f})."
            )
            raise MLEConvergenceError(msg)

        x_current = x_new

        step_norm_m = float(np.linalg.norm(step))
        if step_norm_m < tol_m:
            return MLEResult(
                position=(float(x_current[0]), float(x_current[1])),
                n_iter=iter_idx,
                converged=True,
            )

    msg = (
        f"solve_mle: did not converge -- ||Δx|| >= tol_m={tol_m} "
        f"after max_iter={max_iter} iterations. Last position: "
        f"({float(x_current[0]):.6f}, {float(x_current[1]):.6f})."
    )
    raise MLEConvergenceError(msg)
