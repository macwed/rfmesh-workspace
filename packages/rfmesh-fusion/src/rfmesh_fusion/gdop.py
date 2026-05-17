"""Geometric Dilution of Precision per ADR-007 D4.

GDOP is a *layout-quality* diagnostic: it answers "is the sensor
placement geometrically weak?" *independent* of any individual sensor's
quality. Per-sensor sigma feeds the covariance / Fisher-information
path (``covariance.py``, WS-CD-004) separately. Two diagnostic axes,
two demo-dashboard panels:

* **High GDOP** -> reposition nodes (the layout is bad).
* **Tight ellipse but high per-node residual on node N** -> check node
  N (multipath / calibration on that node).

Mixing the two by weighting GDOP would collapse this diagnostic. The
ADR-007 D4 choice -- unweighted ``H`` -- preserves the separable
axes and aligns with the long-established surveying / GNSS convention
(GDOP, PDOP, HDOP, VDOP and TDOP all defined from geometry alone, with
per-measurement variance entering the covariance step separately).

THE FORMULA
-----------
For an emitter at ``x = (x_e, x_n)`` and nodes ``p_i = (p_e_i,
p_n_i)`` in the local ENU plane, with ``de_i = p_e_i - x_e``,
``dn_i = p_n_i - x_n``, ``r_i^2 = de_i^2 + dn_i^2``::

    H[i, :] = [ -dn_i / r_i^2 ,  de_i / r_i^2 ]            (rad / m)

    GDOP = sqrt( trace( (H^T H)^-1 ) / mean(r_i^2) )

The sign convention of each row follows ``geometry.
bearing_to_unit_vector``'s ``(east, north) = (sin az, cos az)`` mapping;
see ``mle.py``'s "WHY THE JACOBIAN ARG ORDER IS atan2(east, north)"
note for the full derivation. Crucially the *sign* of each row drops
out of ``H^T H`` -- both this module's convention and the CRLB-script
opposite-sign convention (``docs/demo/crlb_analysis.py``) yield the
*identical* ``H^T H`` and therefore the identical GDOP. The trench-demo
test (``test_gdop_matches_crlb_script``) is the gate that confirms
this.

The ``1 / mean(r_i^2)`` normalisation cancels the implicit ``1/r^2`` in
each Jacobian row, leaving a dimensionless, scale-invariant layout
metric: GDOP for an equilateral 3-node triangle around an emitter is
``2/sqrt(3) ~= 1.155`` regardless of the triangle's size in metres.

DEGENERATE-GEOMETRY BEHAVIOUR
-----------------------------
``compute_gdop`` raises ``DegenerateGeometryError`` on three structural
inputs:

1. ``n_nodes < 2`` -- under-determined; ``H`` is at most rank 1.
2. The emitter coincides with any node (``r_i^2 == 0`` for some
   ``i``) -- the Jacobian row at that node is singular and we refuse
   to invent a derivative.
3. ``H^T H`` is rank-deficient (all nodes collinear *and* the emitter
   on the same line) -- ``numpy.linalg.inv`` raises ``LinAlgError``,
   caught and re-raised as ``DegenerateGeometryError``.

Returning ``float("inf")`` on rank-deficient geometry was considered
and rejected. ``inf`` forces every caller to test for it, which is
exactly the silent-degradation path Invariant B3 forbids. Raising
puts the burden on the orchestrator (``fuser.py``, WS-CD-007) to route
to ``fallback_centroid`` per ADR-007 D3 -- mirroring how
``stansfield_seed``'s ``DegenerateGeometryError`` is already handled.

NUMERICAL HYGIENE
-----------------
* ``numpy.linalg.inv`` on the 2x2 ``H^T H`` (not ``pinv`` -- the
  pseudo-inverse would silently return a finite answer on
  rank-deficient input).
* All accumulation in float64.

REFERENCES
----------
* ``docs/adr/ADR-007-fusion-algorithm-choices.md`` D4 (unweighted H,
  range-normalised, standard surveying convention).
* ``INTERFACES.md`` §3 (``FixEvent.gdop`` semantics: strictly positive,
  low ~ favourable, high > ~6 near-collinear).
* ``ARCHITECTURE.md`` §6 (AoA cross-fix, not TDOA).
* ``docs/demo/crlb_analysis.py`` (reference implementation already
  used analytically for the trench-demo CRLB analysis).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .exceptions import DegenerateGeometryError

# Minimum number of nodes for which ``H^T H`` is even potentially
# invertible. With one node ``H`` is shape (1, 2) and ``H^T H`` is
# rank-1; two nodes is the structural floor.
_MIN_NODES: int = 2


def compute_gdop(
    emitter_xy: tuple[float, float],
    node_positions_enu: Sequence[tuple[float, float]],
) -> float:
    """Compute GDOP per ADR-007 D4 -- unweighted, range-normalised.

    Parameters
    ----------
    emitter_xy
        Emitter position ``(east_m, north_m)`` in the local ENU plane.
    node_positions_enu
        Sequence of ``(east_m, north_m)`` node positions in the same
        ENU frame. Length must be ``>= 2``.

    Returns
    -------
    float
        Dimensionless GDOP. Low (~1) means a well-conditioned sensor
        layout; high (``> ~6`` per ``FusionConfig.gdop_warn_threshold``)
        means near-collinear geometry that stretches the position
        covariance regardless of per-bearing sigma. GDOP for an
        equilateral 3-node triangle around the emitter is
        ``2/sqrt(3) ~= 1.155`` exactly.

    Raises
    ------
    DegenerateGeometryError
        * Fewer than two node positions supplied (under-determined).
        * The emitter coincides with any node position (Jacobian
          singular at that bearing).
        * ``H^T H`` is singular -- all nodes collinear with the emitter
          on the same line.

    Notes
    -----
    The function does *not* accept per-bearing sigma. ADR-007 D4 is
    binding: GDOP is unweighted by design, so the layout-quality and
    sensor-quality signals stay separable on the ops dashboard.
    Adding sigma weighting requires a new ADR, not an API extension.
    """
    n = len(node_positions_enu)
    if n < _MIN_NODES:
        msg = (
            f"compute_gdop: requires at least {_MIN_NODES} node positions; "
            f"got {n}. With one node, H^T H is rank-1 and GDOP is undefined "
            "(Invariant B3 -- refuse rather than return inf)."
        )
        raise DegenerateGeometryError(msg)

    nodes_array = np.asarray(node_positions_enu, dtype=np.float64)
    x_e, x_n = float(emitter_xy[0]), float(emitter_xy[1])

    # ``delta_e``, ``delta_n`` follow ``mle.py``'s ``Δe = p_x - x_e``
    # convention. The sign drops out of H^T H, so this is the same
    # GDOP as ``docs/demo/crlb_analysis.py``'s opposite-sign rows.
    delta_e = nodes_array[:, 0] - x_e
    delta_n = nodes_array[:, 1] - x_n
    r_squared = delta_e * delta_e + delta_n * delta_n

    if not bool(np.all(r_squared > 0.0)):
        msg = (
            f"compute_gdop: emitter at ({x_e}, {x_n}) coincides with a "
            "node position; Jacobian row at that node is singular and "
            "GDOP is undefined (Invariant B3 -- refuse rather than "
            "return inf)."
        )
        raise DegenerateGeometryError(msg)

    # H rows: [-Δn / r^2, Δe / r^2]. Sign convention matches ``mle.py``;
    # the opposite sign in ``docs/demo/crlb_analysis.py`` would produce
    # row-wise sign flips, but H^T H is invariant under row sign and so
    # is GDOP -- ``test_gdop_matches_crlb_script`` is the cross-check.
    jacobian = np.empty((n, 2), dtype=np.float64)
    jacobian[:, 0] = -delta_n / r_squared
    jacobian[:, 1] = delta_e / r_squared

    normal_matrix = jacobian.T @ jacobian  # shape (2, 2)

    try:
        normal_inverse = np.linalg.inv(normal_matrix)
    except np.linalg.LinAlgError as exc:
        msg = (
            "compute_gdop: H^T H is singular -- the nodes are collinear "
            "and the emitter lies on the same line, so the bearings span "
            "only one dimension of position. GDOP is undefined; the "
            "orchestrator should route to ``fallback_centroid`` per "
            "ADR-007 D3."
        )
        raise DegenerateGeometryError(msg) from exc

    mean_sq_range = float(np.mean(r_squared))
    trace_inv = float(np.trace(normal_inverse))

    # The normalised quantity is strictly positive on non-degenerate
    # input: ``H^T H`` is positive-definite (the singular case raised
    # above), so its inverse has positive eigenvalues, so its trace is
    # positive. The ``sqrt`` is therefore real-valued; we do not
    # defensively clamp it (the clamp would hide a future bug -- if
    # this expression turned negative, we want to find out).
    return float(np.sqrt(trace_inv / mean_sq_range))
