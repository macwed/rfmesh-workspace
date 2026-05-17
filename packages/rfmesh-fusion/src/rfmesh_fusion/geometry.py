"""Bearing-geometry primitives in the local ENU plane.

Three small, deterministic helpers used by the closed-form Stansfield
solver (``stansfield.py``) and by the ``Fuser`` fallback path
(``fuser.py``, WS-CD-007):

* ``bearing_to_unit_vector`` -- the project-wide azimuth convention
  embodied as a function. Azimuth in degrees, true north = 0, clockwise
  positive -> ``(east, north)`` unit vector. The one place sin/cos for
  rfmesh azimuths happens.
* ``ray_ray_crossing`` -- intersect two oriented rays (a starting point
  + a unit direction each). Returns the crossing point or ``None``
  when the rays are parallel/anti-parallel and no honest answer
  exists.
* ``weighted_centroid_of_crossings`` -- weighted mean of a sequence of
  2-D points. The ``fallback_centroid`` arithmetic per ADR-007 D3.

WHY HAND-ROLLED 2-D INSTEAD OF NUMPY
------------------------------------
These functions operate on at most a handful of 2-vectors. The cost of
``numpy.array`` allocations dwarfs the inner arithmetic at this size,
and the resulting code is also harder to read. Pure Python tuples and
the ``math`` module are faster *and* clearer here -- numpy enters
``rfmesh-fusion`` exactly at the point where it pays off, which is the
2x2 normal-equation solve in ``stansfield.py``.

References
----------
* ``ARCHITECTURE.md`` §6 (AoA, not TDOA; bearings cross at the fix).
* ``INTERFACES.md`` §0 (ENU axes, azimuth convention).
* ``ADR-007-fusion-algorithm-choices.md`` D1 (classical Stansfield in
  ENU), D3 (degenerate-geometry path -> fallback centroid).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Parallelism threshold for ``ray_ray_crossing``.
#
# The 2x2 direction matrix for two unit vectors has determinant
# ``sin(angle_between)``. ``1e-12`` corresponds to roughly ``1e-12 rad``
# (~6e-11 degrees) of angular separation -- well below any physically
# meaningful resolution and below the float64 round-off of a unit-vector
# cross product. Anything tighter would just flag floating-point noise
# as a "real" near-parallel case.
_PARALLEL_DET_THRESHOLD: float = 1e-12


def bearing_to_unit_vector(azimuth_deg: float) -> tuple[float, float]:
    """Convert an azimuth in degrees to a unit ``(east, north)`` vector.

    The project-wide convention is azimuth in degrees, true north = 0,
    clockwise positive. So azimuth ``0`` points along ``+north``,
    azimuth ``90`` along ``+east``, etc. The mapping is therefore::

        east  = sin(azimuth)
        north = cos(azimuth)

    The function does not normalise the input range: ``math.radians``
    composed with ``math.sin``/``math.cos`` is exactly periodic, so
    ``azimuth_deg = 720`` and ``azimuth_deg = -360`` produce the same
    vector as ``azimuth_deg = 0`` to within float64 round-off. In
    production callers, ``BearingReport.azimuth_deg`` is validated to
    ``[0, 360)`` upstream, so this never matters -- but the function
    stays robust to anything that bypasses the validator (e.g. a unit
    test that supplies a raw float).

    Returns
    -------
    tuple[float, float]
        ``(east_component, north_component)``, unit length to within
        float64 round-off.
    """
    theta_rad = math.radians(azimuth_deg)
    return (math.sin(theta_rad), math.cos(theta_rad))


def ray_ray_crossing(
    p1: tuple[float, float],
    u1: tuple[float, float],
    p2: tuple[float, float],
    u2: tuple[float, float],
) -> tuple[float, float] | None:
    """Intersect two oriented rays in the plane.

    Each ray is described by a base point ``p`` and a unit direction
    ``u`` -- ``ray(t) = p + t * u`` for ``t >= 0`` conceptually, though
    this function returns the algebraic intersection of the *lines* the
    rays lie on (the Stansfield/fallback callers handle forward/back
    semantics themselves).

    The 2x2 system to solve is::

        [ u1.x   -u2.x ] [ t1 ]   [ p2.x - p1.x ]
        [ u1.y   -u2.y ] [ t2 ] = [ p2.y - p1.y ]

    with determinant ``u1.x * (-u2.y) - (-u2.x) * u1.y =
    u2.x * u1.y - u1.x * u2.y``. If the absolute determinant is below
    ``_PARALLEL_DET_THRESHOLD`` the rays are parallel (same direction)
    or anti-parallel (opposite direction); in either case the function
    refuses to guess and returns ``None``. The anti-parallel case
    matters: two collinear rays pointing at each other from opposite
    sides have a well-defined geometric meeting somewhere along the
    line, but the function cannot distinguish that from the
    point-at-infinity case where they never meet -- and a closed-form
    solver should not make that judgement call.

    Returns
    -------
    tuple[float, float] | None
        The ``(east, north)`` intersection point in the same frame as
        the inputs, or ``None`` if the rays are parallel or
        anti-parallel.
    """
    p1x, p1y = p1
    u1x, u1y = u1
    p2x, p2y = p2
    u2x, u2y = u2

    # determinant of the 2x2 direction matrix; equal to the cross
    # product (u2 x u1) which is sin(angle_from_u1_to_u2) for unit
    # vectors.
    det = u2x * u1y - u1x * u2y
    if abs(det) < _PARALLEL_DET_THRESHOLD:
        return None

    dx = p2x - p1x
    dy = p2y - p1y

    # Solve for t1; t2 is not needed since we return ``p1 + t1 * u1``.
    # Cramer's rule on the 2x2 system above gives:
    #   t1 = ( (p2.x - p1.x) * (-u2.y) - (-u2.x) * (p2.y - p1.y) ) / det
    #      = ( u2.x * dy - u2.y * dx ) / det
    t1 = (u2x * dy - u2y * dx) / det

    return (p1x + t1 * u1x, p1y + t1 * u1y)


def weighted_centroid_of_crossings(
    crossings: Sequence[tuple[float, float]],
    weights: Sequence[float],
) -> tuple[float, float]:
    """Weighted mean of a sequence of 2-D points.

    Implements the arithmetic of the ``fallback_centroid`` path
    (ADR-007 D3): pairwise ray-ray crossings, weighted by a function of
    their contributing per-bearing weights, are blended into a single
    "best honest guess" emitter position when the closed-form solver
    has refused.

    Parameters
    ----------
    crossings
        Sequence of ``(east, north)`` points. Must be non-empty.
    weights
        Sequence of positive scalar weights, same length as
        ``crossings``. The sum must be strictly positive -- a zero
        weight sum is rejected rather than silently division-by-zeroing
        (Invariant 4).

    Raises
    ------
    ValueError
        ``crossings`` empty, lengths mismatched, or ``sum(weights)``
        is not strictly positive.
    """
    n = len(crossings)
    if n == 0:
        msg = "weighted_centroid_of_crossings requires at least one crossing."
        raise ValueError(msg)
    if len(weights) != n:
        msg = (
            f"weighted_centroid_of_crossings: len(crossings)={n} does not "
            f"match len(weights)={len(weights)}."
        )
        raise ValueError(msg)

    total_weight = math.fsum(weights)
    if total_weight <= 0.0:
        msg = (
            f"weighted_centroid_of_crossings: sum(weights)={total_weight} must "
            f"be strictly positive."
        )
        raise ValueError(msg)

    sum_x = math.fsum(w * c[0] for w, c in zip(weights, crossings, strict=True))
    sum_y = math.fsum(w * c[1] for w, c in zip(weights, crossings, strict=True))

    return (sum_x / total_weight, sum_y / total_weight)
