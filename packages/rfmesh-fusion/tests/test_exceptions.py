"""Tests for the fusion-internal exception hierarchy (WS-CD-002).

One assertion per inheritance edge, per the ticket. The hierarchy is
small on purpose, so the tests are too.
"""

from __future__ import annotations

from rfmesh_fusion.exceptions import DegenerateGeometryError, FusionError


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
