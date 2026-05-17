"""Dashboard layouts: which panels exist and where they live in the figure.

A ``DashboardLayout`` is a frozen tuple of ``PanelSpec`` entries; each
spec names a ``Panel`` subclass and the ``add_subplot`` kwargs that
position it on a ``matplotlib.figure.Figure``. The ``Dashboard``
consumes a ``DashboardLayout`` and instantiates one of each panel
against a freshly-allocated Axes.

Three built-in layouts:

* ``DEMO_LAYOUT_TRENCH`` -- BoTH3 jury demo. Five panels: FixPanel,
  BearingsPanel, ResidualsPanel, ClassificationOverlayPanel,
  NullSteeringPanel. Maps to the four-beat trench-demo narrative.
* ``DEMO_LAYOUT_DEBUG`` -- all nine panels in a 3 x 3 grid. Big-screen
  layout for bench-side debugging.
* ``DEMO_LAYOUT_MINIMAL`` -- the FixPanel alone. Sanity layout / smoke
  test layout / what you ship in a screenshot when everything else has
  caught fire.

Layouts use matplotlib's ``GridSpec``-style ``add_subplot`` kwargs
(``nrows``, ``ncols``, ``index`` style or explicit ``GridSpec`` slot
via ``subplot_kwargs={"rowspan", "colspan", "row", "col"}``). The
Dashboard reads ``rows`` / ``cols`` from the layout's
``grid_shape`` to allocate the GridSpec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rfmesh_ops.panels import (
    BearingsPanel,
    ClassificationOverlayPanel,
    FixPanel,
    GdopHeatmapPanel,
    L1vsL2Panel,
    NodeStatusPanel,
    NullSteeringPanel,
    Panel,
    PseudospectrumPanel,
    ResidualsPanel,
)


@dataclass(frozen=True)
class PanelSpec:
    """One panel's placement on the dashboard.

    Attributes:
        panel_cls: A ``Panel`` subclass. The dashboard instantiates it
            against the Axes carved from ``subplot_kwargs``.
        subplot_kwargs: Keyword arguments forwarded to matplotlib's
            ``Figure.add_subplot(**kwargs)`` indirectly via a GridSpec
            slot. Required keys: ``row``, ``col``. Optional: ``rowspan``,
            ``colspan`` (default 1), ``projection`` (default None /
            rectilinear).
    """

    panel_cls: type[Panel]
    subplot_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DashboardLayout:
    """A complete layout: panel specs + figure grid shape.

    The ``grid_shape`` is a (rows, cols) tuple that defines the
    ``GridSpec`` resolution; each ``PanelSpec.subplot_kwargs`` slots
    into this grid. Panels' (row, col, rowspan, colspan) slots must be
    inside the grid and must not overlap with each other (the
    dashboard's ``_check_layout_integrity`` enforces this).
    """

    panels: tuple[PanelSpec, ...]
    grid_shape: tuple[int, int] = (1, 1)


def _check_no_overlap(layout: DashboardLayout) -> None:
    """Raise ValueError if two PanelSpecs claim the same grid cell.

    The dashboard's layout-integrity test asserts this at construction
    time; calling it from the module's binding constants below also
    guarantees that the built-in layouts cannot be constructed in a
    broken state.
    """
    occupied: set[tuple[int, int]] = set()
    rows, cols = layout.grid_shape
    for spec in layout.panels:
        kw = spec.subplot_kwargs
        row = int(kw.get("row", 0))
        col = int(kw.get("col", 0))
        rowspan = int(kw.get("rowspan", 1))
        colspan = int(kw.get("colspan", 1))
        if row < 0 or col < 0 or row + rowspan > rows or col + colspan > cols:
            msg = (
                f"PanelSpec {spec.panel_cls.__name__} out of grid bounds "
                f"(row={row}, col={col}, rowspan={rowspan}, colspan={colspan}, "
                f"grid_shape={layout.grid_shape})."
            )
            raise ValueError(msg)
        for r in range(row, row + rowspan):
            for c in range(col, col + colspan):
                cell = (r, c)
                if cell in occupied:
                    msg = (
                        f"PanelSpec {spec.panel_cls.__name__} overlaps an "
                        f"earlier panel at cell {cell}."
                    )
                    raise ValueError(msg)
                occupied.add(cell)


# -- DEMO_LAYOUT_TRENCH ---------------------------------------------------
# The BoTH3 jury demo. Six panels in a 4 x 2 grid:
#
#   +---------------+----------------+
#   |   FixPanel    | Pseudospectrum |   row 0
#   |  (large 2x1)  +----------------+
#   |               |  Bearings      |   row 1
#   +---------------+----------------+
#   |  ClsOverlay   |  NullSteering  |   row 2
#   +---------------+----------------+
#   |  Residuals (colspan=2)         |   row 3
#   +---------------+----------------+
#
# FixPanel + percentage display       -> Advantage #1 / #6
# PseudospectrumPanel                 -> Advantage #2 / #3 (binds script Beat D)
# BearingsPanel sigma wedges          -> Advantage #2
# ClassificationOverlayPanel          -> Advantage #5
# NullSteeringPanel                   -> Advantage #4
# ResidualsPanel outlier highlight    -> Advantage #6
#
# Pseudospectrum slot added 2026-05-18 per demo-integrity council finding F3:
# script.md Beat D narrates "on the right tile you can see the MUSIC
# pseudospectrum" — the panel needed to be in the trench layout, not just
# the DEBUG layout.
DEMO_LAYOUT_TRENCH = DashboardLayout(
    panels=(
        PanelSpec(panel_cls=FixPanel, subplot_kwargs={"row": 0, "col": 0, "rowspan": 2}),
        PanelSpec(panel_cls=PseudospectrumPanel, subplot_kwargs={"row": 0, "col": 1}),
        PanelSpec(panel_cls=BearingsPanel, subplot_kwargs={"row": 1, "col": 1}),
        PanelSpec(panel_cls=ClassificationOverlayPanel, subplot_kwargs={"row": 2, "col": 0}),
        PanelSpec(panel_cls=NullSteeringPanel, subplot_kwargs={"row": 2, "col": 1}),
        PanelSpec(panel_cls=ResidualsPanel, subplot_kwargs={"row": 3, "col": 0, "colspan": 2}),
    ),
    grid_shape=(4, 2),
)
_check_no_overlap(DEMO_LAYOUT_TRENCH)


# -- DEMO_LAYOUT_DEBUG ----------------------------------------------------
# All nine panels in a 3 x 3 grid (one panel per cell). Big-screen
# bench-side layout. Order is the architect-mandated panel list from
# docs/design/ops-architecture.md §2.3.
DEMO_LAYOUT_DEBUG = DashboardLayout(
    panels=(
        PanelSpec(panel_cls=FixPanel, subplot_kwargs={"row": 0, "col": 0}),
        PanelSpec(panel_cls=BearingsPanel, subplot_kwargs={"row": 0, "col": 1}),
        PanelSpec(panel_cls=GdopHeatmapPanel, subplot_kwargs={"row": 0, "col": 2}),
        PanelSpec(panel_cls=PseudospectrumPanel, subplot_kwargs={"row": 1, "col": 0}),
        PanelSpec(panel_cls=L1vsL2Panel, subplot_kwargs={"row": 1, "col": 1}),
        PanelSpec(panel_cls=ResidualsPanel, subplot_kwargs={"row": 1, "col": 2}),
        PanelSpec(panel_cls=NodeStatusPanel, subplot_kwargs={"row": 2, "col": 0}),
        PanelSpec(panel_cls=ClassificationOverlayPanel, subplot_kwargs={"row": 2, "col": 1}),
        PanelSpec(panel_cls=NullSteeringPanel, subplot_kwargs={"row": 2, "col": 2}),
    ),
    grid_shape=(3, 3),
)
_check_no_overlap(DEMO_LAYOUT_DEBUG)


# -- DEMO_LAYOUT_MINIMAL --------------------------------------------------
# Just the FixPanel. Smoke layout / screenshot layout / "what is the
# headline number" layout.
DEMO_LAYOUT_MINIMAL = DashboardLayout(
    panels=(PanelSpec(panel_cls=FixPanel, subplot_kwargs={"row": 0, "col": 0}),),
    grid_shape=(1, 1),
)
_check_no_overlap(DEMO_LAYOUT_MINIMAL)


LAYOUTS_BY_NAME: dict[str, DashboardLayout] = {
    "trench": DEMO_LAYOUT_TRENCH,
    "debug": DEMO_LAYOUT_DEBUG,
    "minimal": DEMO_LAYOUT_MINIMAL,
}


__all__ = [
    "DEMO_LAYOUT_DEBUG",
    "DEMO_LAYOUT_MINIMAL",
    "DEMO_LAYOUT_TRENCH",
    "LAYOUTS_BY_NAME",
    "DashboardLayout",
    "PanelSpec",
]
