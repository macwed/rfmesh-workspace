"""Tests for the fusion-internal exception hierarchy (WS-CD-002, WS-CD-003).

One assertion per inheritance edge, per each ticket. The hierarchy is
small on purpose, so the tests are too.
"""

from __future__ import annotations

from rfmesh_fusion.exceptions import (
    DegenerateGeometryError,
    FusionError,
    MLEConvergenceError,
)


def test_fusion_error_subclasses_exception() -> None:
    """``FusionError`` derives from ``Exception``, not ``BaseException``.

    Inheriting directly from ``BaseException`` would make
    ``except Exception`` blocks silently miss us -- the ticket pins
    ``Exception`` deliberately so the ``Fuser`` (WS-CD-007) can use a
    broad ``except FusionError`` without bypassing the standard
    ``except Exception`` net upstream.
    """
    assert issubclass(FusionError, Exception)


def test_degenerate_geometry_error_subclasses_fusion_error() -> None:
    """``DegenerateGeometryError`` derives from ``FusionError``.

    Lets the ``Fuser`` catch the general base for unanticipated future
    subclasses while the closed-form solver still raises the specific
    one.
    """
    assert issubclass(DegenerateGeometryError, FusionError)


def test_degenerate_geometry_error_subclasses_exception_transitively() -> None:
    """``DegenerateGeometryError`` is, by transitivity, an ``Exception``."""
    assert issubclass(DegenerateGeometryError, Exception)


def test_mle_convergence_error_subclasses_fusion_error() -> None:
    """``MLEConvergenceError`` derives from ``FusionError`` (WS-CD-003).

    Lets the future ``Fuser`` catch the general base
    (``except FusionError``) and still distinguish the iterative-path
    failure from the closed-form path's ``DegenerateGeometryError``.
    """
    assert issubclass(MLEConvergenceError, FusionError)


def test_mle_convergence_error_is_not_degenerate_geometry_error() -> None:
    """``MLEConvergenceError`` is a sibling of ``DegenerateGeometryError``.

    The two represent structurally different failures: rank-deficiency
    ab initio on the closed-form path vs divergence/non-convergence on
    the iterative refinement path. ``fuser.py`` (WS-CD-007) will route
    them to different fallback strategies; we pin the type hierarchy
    here so that future ``except DegenerateGeometryError`` blocks do
    not accidentally swallow MLE divergence (Invariant B3 -- a silent
    swallow there would silently feed a centroid fallback into the MLE
    panel, contradicting ADR-007 D3).
    """
    assert not issubclass(MLEConvergenceError, DegenerateGeometryError)
    assert not issubclass(DegenerateGeometryError, MLEConvergenceError)
