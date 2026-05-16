"""Internal exception hierarchy for ``rfmesh-fusion``.

These exceptions are deliberately *not* part of ``rfmesh-contracts``.
``ARCHITECTURE.md`` §3 fixes the rule: the contracts package stays pure
data + Protocol; exceptions live with the workstream that raises them.
Cross-workstream callers handle errors at the Protocol boundary
(``Fuser.fuse``) -- they do not import these symbols, and the
``Fuser`` implementation catches them locally to drive the
``fallback_centroid`` path (ADR-004 D3).

The hierarchy stays small on purpose. A new fusion-internal exception
class is justified only when a caller in the package genuinely needs to
distinguish it from ``DegenerateGeometryError``; otherwise it is a
``FusionError`` with a descriptive message and an inheriting class is
just noise.
"""

from __future__ import annotations


class FusionError(Exception):
    """Base class for all ``rfmesh-fusion`` internal exceptions.

    Subclasses are caught by the package-level orchestration
    (``fuser.py`` in WS-CD-007) to decide between honest-degraded output
    and refusal. Outside ``rfmesh-fusion`` this class is not part of the
    contract surface; consumers see only ``FixEvent | None`` from
    ``Fuser.fuse``.
    """


class DegenerateGeometryError(FusionError):
    """Raised by the closed-form solver when the geometry cannot be solved.

    Concretely: fewer than the minimum number of bearings, mismatched
    bearings/positions lengths, all rays parallel, all node positions
    coincident, or the 2x2 normal matrix is numerically singular
    (determinant magnitude below ``1e-12`` times the trace squared, or
    ``numpy.linalg.solve`` raised ``LinAlgError``).

    ``fuser.py`` (WS-CD-007) catches this and routes to the
    ``fallback_centroid`` path per ADR-004 D3 -- the closed-form solver
    refuses to invent an answer rather than degrading silently
    (Invariant 4).
    """
