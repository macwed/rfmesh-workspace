"""Tests for ``rfmesh_fusion.gdop`` -- ADR-007 D4 unweighted GDOP.

Each test name points at one property. The trench-demo cross-check
(``test_gdop_matches_crlb_script``) is the load-bearing gate: a
disagreement there means the Jacobian or the formula has drifted from
``docs/demo/crlb_analysis.py``'s reference computation and the bug is
arithmetic, not tolerance.
"""

from __future__ import annotations

import inspect
import math

import pytest
from rfmesh_fusion.exceptions import DegenerateGeometryError
from rfmesh_fusion.gdop import compute_gdop

# Named thresholds to keep the assertions readable and avoid
# PLR2004 "magic value in comparison".
_TWO_NODE_GDOP_UPPER: float = 2.0
_EQUILATERAL_TOLERANCE: float = 0.3
_HIGH_GDOP_THRESHOLD: float = 10.0
_CRLB_DISPLAY_TOLERANCE: float = 0.05  # crlb_analysis.py prints {:.2f}


# ---------------------------------------------------------------------------
# Closed-form sanity geometries
# ---------------------------------------------------------------------------


def test_two_node_symmetric_baseline_low_gdop() -> None:
    """Two nodes equidistant from the emitter on a perpendicular baseline.

    Emitter at (0, 1000), nodes at (-1000, 0) and (+1000, 0). The two
    bearings are at +/-45 deg relative to north, the geometry is
    well-conditioned, and the range-normalised GDOP is sqrt(2) for this
    symmetric two-node case (closed-form: H^T H is diagonal, both
    eigenvalues equal 1/(2 r^2), trace inverse = 4 r^2, mean r^2 = 2 r^2,
    GDOP = sqrt(4 r^2 / 2 r^2) = sqrt(2)).
    """
    emitter = (0.0, 1000.0)
    nodes = [(-1000.0, 0.0), (+1000.0, 0.0)]
    gdop = compute_gdop(emitter, nodes)
    assert gdop == pytest.approx(math.sqrt(2.0), abs=1e-9)
    # And in the operator-sensible "bounded range" the ticket asks for.
    assert 1.0 < gdop < _TWO_NODE_GDOP_UPPER


def test_three_node_equilateral_low_gdop() -> None:
    """Three nodes at the vertices of an equilateral triangle around the emitter.

    Emitter at origin, nodes on a circle of radius ``r`` at 90 deg,
    210 deg, 330 deg (math-convention angles). The closed-form GDOP for
    this geometry is ``2/sqrt(3) ~= 1.155`` exactly -- the "ideal"
    three-node layout that the ticket asks be checked within 0.3 of 1.0.
    """
    radius = 1000.0
    angles_deg = (90.0, 210.0, 330.0)
    nodes = [
        (radius * math.cos(math.radians(a)), radius * math.sin(math.radians(a))) for a in angles_deg
    ]
    emitter = (0.0, 0.0)

    gdop = compute_gdop(emitter, nodes)
    expected = 2.0 / math.sqrt(3.0)

    assert gdop == pytest.approx(expected, abs=1e-9)
    # And the ticket-level "ideal-layout" tolerance.
    assert abs(gdop - 1.0) < _EQUILATERAL_TOLERANCE


def test_gdop_scale_invariant() -> None:
    """GDOP is invariant under uniform scaling of the geometry.

    Scaling every node-emitter offset by ``k`` scales each ``H`` row by
    ``1/k`` and each range by ``k``; the ``trace((H^T H)^-1) / mean(r^2)``
    ratio cancels both. The range-normalisation is the *whole point* --
    GDOP captures layout shape, not absolute size.
    """
    emitter = (0.0, 0.0)
    nodes_small = [(1000.0, 0.0), (-500.0, 866.0), (-500.0, -866.0)]
    nodes_large = [(p[0] * 5.0, p[1] * 5.0) for p in nodes_small]

    gdop_small = compute_gdop(emitter, nodes_small)
    gdop_large = compute_gdop(emitter, nodes_large)
    assert gdop_small == pytest.approx(gdop_large, rel=1e-12)


# ---------------------------------------------------------------------------
# Pathological geometries
# ---------------------------------------------------------------------------


def test_near_collinear_high_gdop() -> None:
    """Three collinear nodes; emitter beyond the baseline with small offset.

    Nodes at (-1000, 0), (0, 0), (+1000, 0) on the east axis. Emitter
    at (2000, 50) -- well past the baseline endpoint with a small
    perpendicular offset. All three bearings cluster within a narrow
    angular fan and ``H^T H`` is near-singular; GDOP is comfortably
    above 10 (empirically ~32 for this exact case, but the test only
    asserts the qualitative "stretches the ellipse" threshold of >= 10).
    """
    nodes = [(-1000.0, 0.0), (0.0, 0.0), (+1000.0, 0.0)]
    emitter = (2000.0, 50.0)
    gdop = compute_gdop(emitter, nodes)
    assert gdop >= _HIGH_GDOP_THRESHOLD


def test_rank_deficient_geometry_raises() -> None:
    """Three nodes truly collinear *and* emitter on the same line.

    ``H`` has rank 1 (every bearing row is a scalar multiple of the
    line's unit normal), ``H^T H`` is singular, and the function
    refuses to invent an answer (Invariant B3). This is the
    "exactly-on-the-line" companion to ``test_near_collinear_high_gdop``.
    """
    nodes = [(-1000.0, 0.0), (0.0, 0.0), (+1000.0, 0.0)]
    emitter = (2000.0, 0.0)
    with pytest.raises(DegenerateGeometryError, match="singular"):
        compute_gdop(emitter, nodes)


def test_emitter_on_node_raises() -> None:
    """Emitter coincides with a node position -> raise.

    ``r_i^2 == 0`` at that node makes the Jacobian row a 0/0
    expression. The function refuses rather than dividing by zero;
    Invariant B3 (the docstring documents this explicitly).
    """
    nodes = [(0.0, 0.0), (1000.0, 0.0), (0.0, 1000.0)]
    emitter = (0.0, 0.0)  # exactly on node 0
    with pytest.raises(DegenerateGeometryError, match="coincides with a node"):
        compute_gdop(emitter, nodes)


def test_two_nodes_minimum() -> None:
    """One-node input is structurally insufficient -> raise.

    A single bearing row makes ``H^T H`` rank-1; there is no GDOP to
    compute. The function refuses; the orchestrator's
    ``min_bearings_for_fix`` floor (FusionConfig, default 2) catches
    this upstream in production but ``compute_gdop`` is correct in
    isolation too.
    """
    with pytest.raises(DegenerateGeometryError, match="at least 2"):
        compute_gdop((0.0, 1000.0), [(0.0, 0.0)])


def test_zero_nodes_raises() -> None:
    """Empty node list -> raise.

    Same path as the one-node test but worth pinning separately so a
    future refactor that drops the ``<`` to ``<=`` comparator is caught.
    """
    with pytest.raises(DegenerateGeometryError, match="at least 2"):
        compute_gdop((0.0, 1000.0), [])


# ---------------------------------------------------------------------------
# Encoding ADR-007 D4: unweighted by design
# ---------------------------------------------------------------------------


def test_unweighted_independent_of_sigma() -> None:
    """``compute_gdop`` does not accept sigma; same geometry -> same GDOP.

    Two checks, both encoding the ADR-007 D4 unweighted decision:

    1. *Signature inspection.* The function exposes exactly two
       positional parameters (``emitter_xy``, ``node_positions_enu``)
       and no ``sigma`` / ``weights`` / ``bearings`` parameter.
       Renaming or extending the signature to take per-bearing sigma
       requires a new ADR; this assertion is the tripwire.
    2. *Behavioural.* For one fixed geometry, repeated calls under
       hypothetical "different sigma scenarios" (which the caller
       cannot actually pass in, but that is the point) yield byte-
       identical GDOP. This is trivially true given (1), but it
       documents the operational invariant.
    """
    sig = inspect.signature(compute_gdop)
    param_names = list(sig.parameters)
    assert param_names == ["emitter_xy", "node_positions_enu"]
    forbidden = {"sigma", "sigmas", "sigma_deg", "weights", "bearings"}
    assert not (set(param_names) & forbidden), (
        f"compute_gdop should not accept any of {forbidden}; "
        f"ADR-007 D4 makes GDOP unweighted by design."
    )

    emitter = (0.0, 0.0)
    nodes = [(1000.0, 0.0), (-500.0, 866.0), (-500.0, -866.0)]
    gdop_a = compute_gdop(emitter, nodes)
    gdop_b = compute_gdop(emitter, nodes)
    assert gdop_a == gdop_b


# ---------------------------------------------------------------------------
# Cross-check against the trench-demo CRLB analysis script
# ---------------------------------------------------------------------------


# Trench-demo geometry from ``docs/demo/trench-demo-geometry.md`` §2.2
# and ``docs/demo/crlb_analysis.py``. ENU frame, emitter at (0, 3000).
_EMITTER_TRENCH: tuple[float, float] = (0.0, 3000.0)
_NODE_A: tuple[float, float] = (-1800.0, +1800.0)  # NW flank
_NODE_B: tuple[float, float] = (+1800.0, +1800.0)  # NE flank
_NODE_C: tuple[float, float] = (0.0, +900.0)  # central south
_NODE_D: tuple[float, float] = (+2000.0, +3500.0)  # L2 overwatch, NE


def test_gdop_matches_crlb_script_beat_c() -> None:
    """Beat C: three L1 nodes -> GDOP = 1.16 per ``crlb_analysis.py``.

    The tolerance (``abs=0.05``) is the display rounding of the CRLB
    script (``{:.2f}``), not a hand-tuned slack -- the implementations
    use the same formula and same Jacobian (modulo sign convention,
    which drops out of ``H^T H``); they agree to many more decimals
    than ``0.05`` in practice.
    """
    gdop = compute_gdop(_EMITTER_TRENCH, [_NODE_A, _NODE_B, _NODE_C])
    assert gdop == pytest.approx(1.16, abs=_CRLB_DISPLAY_TOLERANCE)


def test_gdop_matches_crlb_script_beat_d() -> None:
    """Beat D: three L1 + one L2 -> GDOP = 1.02 per ``crlb_analysis.py``.

    Adding node D (NE overwatch) slightly improves the geometric
    conditioning vs Beat C alone -- the four nodes now span a larger
    angular fan around the emitter, and GDOP drops from 1.16 to 1.02.
    """
    gdop = compute_gdop(_EMITTER_TRENCH, [_NODE_A, _NODE_B, _NODE_C, _NODE_D])
    assert gdop == pytest.approx(1.02, abs=_CRLB_DISPLAY_TOLERANCE)
