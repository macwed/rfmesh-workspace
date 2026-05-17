"""Position covariance from Fisher information + 95% confidence ellipse.

This module closes the *honesty payload* half of a ``FixEvent``: given
the MLE-converged emitter position from ``solve_mle`` (WS-CD-003) and
the same batch of ``BearingReport`` s + ENU node positions, it returns

* ``compute_covariance`` -> the 2x2 position covariance
  ``Sigma = (J^T W J)^-1`` in metres squared, evaluated at the converged
  emitter, with ``J`` the analytic Jacobian (radians per metre) shared
  with ``mle.py`` and ``W = diag(1 / sigma_i_rad^2)`` (sigmas converted
  from degrees to radians *once*).
* ``covariance_to_ellipse`` -> the 95% confidence ``EllipseENU``,
  built by eigendecomposing Sigma, sorting eigenvalues descending, and
  scaling each by ``sqrt(χ²_{0.95, df=2}) ≈ 2.448`` -- the
  load-bearing factor ADR-009 exists to make impossible to forget.

WHY THIS LIVES IN ITS OWN MODULE
--------------------------------
The covariance + ellipse pair is the part of a ``FixEvent`` that the
dashboard and the CoT publisher render verbatim. ``ARCHITECTURE.md``
§7 calls out the "ellipse shrinks as nodes are added" demo behaviour;
``INTERFACES.md`` §2 fixes the 95% chi-square contract; ADR-009
records the correction of an earlier 1-sigma vs 95% scale mix-up. Putting
the chi-square constant, the eigendecomposition, and the
EllipseENU-orientation convention all in one place means a future
reviewer (or a future agent) sees the whole honesty chain in one
file, with the ADR cross-references inline.

CHOICE OF ALGORITHM
-------------------
Per ADR-007 D5: closed-form 2x2 eigendecomposition of the covariance,
``χ²_{0.95, df=2} = 5.991464547107979`` hard-coded inline. The
chi-square value is a fixed constant of the universe -- importing
``scipy.stats.chi2.ppf`` at runtime would add a dependency for one
literal, which would never change. The constant sits next to a
comment that names the scipy expression for reviewer verification
*and* names ADR-009 as the reason this scale (not 1-sigma) is the
load-bearing choice.

JACOBIAN CONVENTION -- IDENTICAL TO ``mle.py``
----------------------------------------------
The project's azimuth convention is fixed by
``geometry.bearing_to_unit_vector``: azimuth in degrees, true north =
0, CW positive; ``(east, north) = (sin(az), cos(az))``. The predicted
azimuth from a node at ``(p_e, p_n)`` to an emitter at ``(x_e, x_n)``
is therefore ``atan2(x_e - p_e, x_n - p_n)`` -- east offset first,
**not** the maths-textbook ``atan2(y, x)`` with ``y = north``. With
offsets ``Δe = p_e - x_e``, ``Δn = p_n - x_n``, ``r^2 = Δe^2 + Δn^2``,
the Jacobian row reads:

::

    J[i, :] = [ -Δn / r^2 ,  Δe / r^2 ]
            = [ (x_n - p_n) / r^2 , -(x_e - p_e) / r^2 ]

This is byte-identical to ``mle._predicted_azimuths_and_jacobian``
and to ``docs/demo/crlb_analysis.py:jac_row``. Get it wrong here and
every confidence ellipse the system publishes is silently biased --
which is why the ``test_trench_demo_geometry_reproduces_crlb`` test
in ``test_covariance.py`` exists.

SIGMA UNITS (load-bearing)
--------------------------
``BearingReport.azimuth_sigma_deg`` is in **degrees**; the Jacobian
is in **radians per metre**. The weights ``W = diag(1 / sigma^2)`` must
be in ``rad^-2``, otherwise the covariance comes out off by
``(π/180)^2 ≈ 3.05e-4`` across the board -- i.e. every ellipse would
be ~57x too small, silently demolishing the honesty payload. Convert
ONCE, outside any loop. (``stansfield.py`` deliberately keeps deg^2
because the ``(π/180)^2`` factor cancels exactly out of *its* normal
equations; covariance does **not** have that cancellation -- the
covariance *is* the matrix the weights produce.)

References
----------
* ``ADR-007-fusion-algorithm-choices.md`` D5 (closed-form 2x2
  eigendecomposition, ``χ²_{0.95, df=2}`` inline constant).
* ``ADR-009-confidence-band-math-correction-and-demo-narrative.md``
  (1-sigma vs 95% scale; this is the reason the chi-square constant is
  the load-bearing number).
* ``INTERFACES.md`` §2 (``EllipseENU`` -- 95% contour, semi_major >=
  semi_minor, orientation_deg from East toward North in [-180,
  +180]°).
* ``INTERFACES.md`` §3 (``FixEvent.covariance_m2`` semantics).
* ``ARCHITECTURE.md`` §7 (honesty payload on every fix).
* ``docs/demo/trench-demo-geometry.md`` §2 + ``docs/demo/crlb_analysis.py``
  (the ground-truth numbers ``compute_covariance`` reproduces in
  ``test_trench_demo_geometry_reproduces_crlb``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    EllipseENU,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)

from .exceptions import SingularFisherInformationError

# 2-DOF 95% chi-square quantile.
#
# Reviewer verification: scipy.stats.chi2.ppf(0.95, df=2)
#   -> 5.991464547107979
#
# This is the load-bearing scale that converts 1-sigma principal variances
# of the position covariance into the 95% confidence-ellipse semi-axes
# the EllipseENU contract requires (INTERFACES.md §2). ADR-009 records
# the historical 1-sigma-vs-95% mix-up that this constant is here to make
# impossible going forward: the back-of-envelope in ADR-005's
# "Rationale for the 5% choice" gave the 1-sigma semi-major, the contract
# expects the 95% semi-major, the factor between them is exactly
# sqrt(this constant) ≈ 2.448. Forgetting it makes every published
# ellipse ~2.45x too small -- the silent honesty failure ADR-009
# corrects in the demo narrative.
#
# This comment is binding (per WS-CD-004 acceptance #6) -- do not
# remove or paraphrase without a corresponding ADR.
CHI2_95_DF2: float = 5.991464547107979

# Minimum number of bearings for a meaningful covariance. Matches
# ``solve_mle`` and ``stansfield_seed``: one bearing is a ray, not a
# fix; two bearings give a unique crossing on non-degenerate geometry
# but a covariance with two free parameters needs at least two
# constraints.
_MIN_BEARINGS: int = 2


def _inverse_variance_weights_rad(bearings: Sequence[BearingReport]) -> np.ndarray:
    """Return ``1 / sigma_rad^2`` for each bearing as a 1-D float64 array.

    Local re-derivation of the same convention ``mle._inverse_variance_
    weights_rad`` uses -- the helper there is module-private so we
    mirror the formula rather than import a private symbol. Both
    derivations agree because the conversion ``deg -> rad`` is one
    NumPy call (``np.radians``) and the squared-inverse is closed-form.

    The contract validator enforces ``azimuth_sigma_deg > 0`` (Invariant
    B3 -- no defensive ``if sigma <= 0`` fallback; an invalid sigma is
    a broken contract upstream and we trust the validator).
    """
    sigma_deg = np.array(
        [b.azimuth_sigma_deg for b in bearings],
        dtype=np.float64,
    )
    sigma_rad = np.radians(sigma_deg)
    return 1.0 / (sigma_rad * sigma_rad)


def _jacobian_at(
    emitter_xy: tuple[float, float],
    node_positions_enu: np.ndarray,
) -> np.ndarray:
    """Return the analytic Jacobian ``J`` at ``emitter_xy`` (radians / metre).

    Shape: ``(n, 2)``. Row ``i`` for node ``i`` at ``(p_e, p_n)`` is

    ::

        J[i, :] = [ -Δn / r^2 ,  Δe / r^2 ]

    with ``Δe = p_e - x_e``, ``Δn = p_n - x_n``, ``r^2 = Δe^2 + Δn^2``.
    Byte-identical to ``mle._predicted_azimuths_and_jacobian``'s
    Jacobian (and to ``docs/demo/crlb_analysis.py:jac_row``) -- a sign
    flip between any of the three would silently bias every published
    ellipse, which is why ``test_trench_demo_geometry_reproduces_crlb``
    exists as the end-to-end gate.

    Raises
    ------
    SingularFisherInformationError
        If any node sits exactly on the emitter (``r^2 = 0``). In
        practice ``stansfield_seed`` raises ``DegenerateGeometryError``
        on the same input long before this stage, but the check here
        is the local Invariant-B3 safety net: refuse to invent a
        derivative at a singularity.
    """
    x_e, x_n = emitter_xy
    delta_e = node_positions_enu[:, 0] - x_e
    delta_n = node_positions_enu[:, 1] - x_n
    r_squared = delta_e * delta_e + delta_n * delta_n

    if not np.all(r_squared > 0.0):
        msg = (
            "compute_covariance: emitter coincides with a node "
            f"(emitter at {emitter_xy}); Jacobian row is singular and "
            "the Fisher information matrix is rank-deficient (Invariant "
            "B3 -- refusing to invent a derivative)."
        )
        raise SingularFisherInformationError(msg)

    jacobian = np.empty((node_positions_enu.shape[0], 2), dtype=np.float64)
    jacobian[:, 0] = -delta_n / r_squared
    jacobian[:, 1] = delta_e / r_squared
    return jacobian


def compute_covariance(
    emitter_xy: tuple[float, float],
    bearings: Sequence[BearingReport],
    node_positions_enu: Sequence[tuple[float, float]],
) -> np.ndarray:
    """Return the 2x2 position covariance at the MLE-converged emitter.

    ``Sigma = (J^T W J)^-1`` in m², with ``J`` the analytic Jacobian of
    predicted azimuths w.r.t. ``(east, north)`` emitter coordinates
    (radians per metre), and ``W = diag(1 / sigma_i_rad^2)``. Sigma is
    converted from degrees to radians once before forming ``W`` (same
    convention as ``solve_mle``).

    Parameters
    ----------
    emitter_xy
        Converged emitter position ``(east_m, north_m)`` in the same
        ENU frame as ``node_positions_enu``. Per ADR-007 D2 this is the
        MLE output (``solve_mle``-returned), not the Stansfield seed --
        the seed is finite-sample biased; the converged position is the
        argmax of the likelihood whose Hessian inverse *is* the
        covariance. The function does not call ``solve_mle`` itself;
        the caller (eventually ``StansfieldMLEFuser`` in WS-CD-007)
        supplies the converged position.
    bearings
        ``>= 2`` ``BearingReport`` objects, in the same order as
        ``node_positions_enu``.
    node_positions_enu
        ENU coordinates of the corresponding nodes. Length must equal
        ``len(bearings)``. Caller has already projected from WGS-84
        via ``projection.to_enu``.

    Returns
    -------
    np.ndarray
        ``(2, 2)`` float64 symmetric PSD covariance matrix in m²,
        layout ``[[sigma_xx, sigma_xy], [sigma_xy, sigma_yy]]``. Convert to the
        ``FixEvent.covariance_m2`` tuple as
        ``(cov[0, 0], cov[0, 1], cov[1, 1])``.

    Raises
    ------
    SingularFisherInformationError
        If ``len(bearings) < 2`` or lengths do not match; if any node
        coincides with the emitter (zero-range Jacobian row); if
        ``J^T W J`` is numerically singular (``numpy.linalg.solve``
        raises ``LinAlgError``). All three are Invariant-B3 loud
        failures: covariance refuses to invent ``inf`` / ``nan``
        entries.
    """
    n = len(bearings)
    if n < _MIN_BEARINGS:
        msg = f"compute_covariance: requires at least {_MIN_BEARINGS} bearings; got {n}."
        raise SingularFisherInformationError(msg)
    if len(node_positions_enu) != n:
        msg = (
            f"compute_covariance: len(bearings)={n} does not match "
            f"len(node_positions_enu)={len(node_positions_enu)}."
        )
        raise SingularFisherInformationError(msg)

    nodes_array = np.array(node_positions_enu, dtype=np.float64)
    weights = _inverse_variance_weights_rad(bearings)  # rad^-2

    jacobian = _jacobian_at(emitter_xy, nodes_array)

    # Weighted Fisher information: F = J^T W J. Same hygiene as
    # ``solve_mle``: ``(J.T * w) @ J`` avoids forming diag(W)
    # explicitly. F is 2x2, symmetric, PSD by construction (J is real,
    # W is diagonal-positive).
    jtw = jacobian.T * weights
    fisher = jtw @ jacobian  # shape (2, 2)

    # Sigma = F^-1 via ``np.linalg.solve`` against the 2x2 identity. Same
    # hygiene rule as ``stansfield.py`` and ``mle.py``: prefer ``solve``
    # over ``inv``-then-multiply for the better-conditioned numerics.
    # On singular F we wrap LinAlgError into the domain exception so
    # callers (and tests) see one uniform failure mode.
    try:
        covariance = np.linalg.solve(fisher, np.eye(2, dtype=np.float64))
    except np.linalg.LinAlgError as exc:
        msg = (
            "compute_covariance: Fisher information matrix J^T W J is "
            f"singular (numpy.linalg.solve: {exc}); the geometry is "
            "rank-deficient -- this should not be reached in practice "
            "because stansfield_seed raises DegenerateGeometryError on "
            "the same input upstream."
        )
        raise SingularFisherInformationError(msg) from exc

    # Symmetrise to wash out the floating-point asymmetry the inversion
    # introduces (the inverse of a symmetric matrix is symmetric in
    # exact arithmetic; rounding makes it ~1e-15 off). Cheap and keeps
    # downstream ``eigh`` calls honest.
    covariance = 0.5 * (covariance + covariance.T)
    return covariance


def covariance_to_ellipse(cov_2x2: np.ndarray) -> EllipseENU:
    """Convert a 2x2 position covariance to a 95% confidence ``EllipseENU``.

    Eigendecomposes ``Sigma``, sorts eigenvalues descending, scales each by
    ``sqrt(CHI2_95_DF2)`` to obtain the semi-axes, and derives the
    orientation from the major-axis eigenvector as ``atan2(north_comp,
    east_comp)`` in degrees -- exactly the ``EllipseENU`` convention
    (orientation measured from local East toward North in the ENU
    plane, range ``[-180, +180]°``).

    Parameters
    ----------
    cov_2x2
        Symmetric PSD ``(2, 2)`` float covariance matrix in m². Typically
        the output of ``compute_covariance``; the function is also
        defined for any caller-supplied PSD matrix (e.g. a closed-form
        CRLB test fixture). Layout
        ``[[sigma_xx, sigma_xy], [sigma_xy, sigma_yy]]``.

    Returns
    -------
    EllipseENU
        A frozen, contract-validated ellipse. ``semi_major_m >=
        semi_minor_m`` is guaranteed by sorting eigenvalues descending
        before constructing the model (otherwise the Pydantic validator
        raises -- the swap is a real error, not a paper-over).

    Raises
    ------
    SingularFisherInformationError
        If ``cov_2x2`` is not a ``(2, 2)`` matrix, or any eigenvalue
        is non-positive (the matrix is non-PSD or rank-deficient). This
        is an Invariant-B3 loud failure: covariance refuses to clamp a
        negative eigenvalue to zero, refuses to take ``sqrt`` of a
        negative number, refuses to return a degenerate ellipse with
        ``semi_minor_m = 0``. A non-PSD covariance is a structural
        input error and we surface it.
    """
    if cov_2x2.shape != (2, 2):
        msg = f"covariance_to_ellipse: expected a (2, 2) matrix; got shape {cov_2x2.shape}."
        raise SingularFisherInformationError(msg)

    # ``np.linalg.eigh`` returns eigenvalues in *ascending* order and
    # treats the input as symmetric (it only reads the lower triangle).
    # We sort descending so the major axis is unambiguously first.
    eigvals, eigvecs = np.linalg.eigh(cov_2x2)
    order = np.argsort(eigvals)[::-1]
    lambdas = eigvals[order]
    vecs = eigvecs[:, order]

    # Both eigenvalues must be strictly positive. The smaller one
    # (``lambdas[1]``) is the gate: if it is non-positive the matrix is
    # rank-deficient or non-PSD; if the larger one (``lambdas[0]``) is
    # non-positive the matrix is the zero matrix (every eigenvalue
    # zero) and we still refuse. The ``EllipseENU`` Pydantic validator
    # rejects ``semi_*_m <= 0`` regardless, but we raise a more
    # informative domain exception here so the caller sees the cause
    # ("non-PSD covariance"), not just "semi_minor_m must be > 0".
    if lambdas[1] <= 0.0 or lambdas[0] <= 0.0:
        msg = (
            "covariance_to_ellipse: covariance is non-PSD or rank-"
            f"deficient (eigenvalues {lambdas.tolist()}); cannot derive "
            "honest 95%-confidence semi-axes (Invariant B3 -- refusing "
            "to clamp or take sqrt of a non-positive eigenvalue)."
        )
        raise SingularFisherInformationError(msg)

    semi_major_m = math.sqrt(CHI2_95_DF2 * float(lambdas[0]))
    semi_minor_m = math.sqrt(CHI2_95_DF2 * float(lambdas[1]))

    # The major-axis eigenvector is the first column of the
    # reordered ``vecs``. Components are ``(east, north)`` because
    # the covariance is laid out in ENU (the input convention is
    # fixed by ``compute_covariance``'s Jacobian, which takes
    # ``(east, north)`` partial derivatives).
    #
    # ``orientation_deg`` is measured from local East toward North in
    # the ENU plane (INTERFACES.md §2). That is exactly the
    # ``atan2(north_component, east_component)`` form, in degrees.
    # Eigenvectors are sign-arbitrary -- ``v`` and ``-v`` represent
    # the same axis -- so we collapse to the half-plane that gives a
    # canonical angle: when ``east_component < 0`` we flip the
    # vector so the angle lands in the right half of [-90, +90] when
    # the axis is "horizontal-ish". This is purely cosmetic; the
    # ellipse geometry is identical either way (the ellipse is
    # symmetric under v -> -v rotation).
    major_vec = vecs[:, 0].astype(np.float64, copy=False)
    east_comp = float(major_vec[0])
    north_comp = float(major_vec[1])
    if east_comp < 0.0:
        east_comp = -east_comp
        north_comp = -north_comp
    orientation_deg = math.degrees(math.atan2(north_comp, east_comp))

    # ``math.atan2`` returns ``[-pi, pi]`` so ``orientation_deg`` is
    # already in ``[-180, +180]`` -- the ``EllipseENU`` validator's
    # range. No further wrapping needed.

    return EllipseENU(
        semi_major_m=semi_major_m,
        semi_minor_m=semi_minor_m,
        orientation_deg=orientation_deg,
    )
