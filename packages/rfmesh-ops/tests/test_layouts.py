"""Tests for rfmesh_ops.layouts -- built-in layouts construct cleanly."""

from __future__ import annotations

import pytest
from rfmesh_ops.layouts import (
    DEMO_LAYOUT_DEBUG,
    DEMO_LAYOUT_MINIMAL,
    DEMO_LAYOUT_TRENCH,
    DashboardLayout,
    PanelSpec,
    _check_no_overlap,
)
from rfmesh_ops.panels import (
    BearingScanPanel,
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


def test_demo_layout_trench_builds() -> None:
    """DEMO_LAYOUT_TRENCH carries the 6 jury panels in a 4x2 grid.

    Updated 2026-05-18 (D5 council-audit close-out): PseudospectrumPanel
    was added to bind the script.md Beat D narration. Grid extended from
    3x2 to 4x2 to fit it.
    """
    layout = DEMO_LAYOUT_TRENCH
    assert isinstance(layout, DashboardLayout)
    panel_classes = {spec.panel_cls for spec in layout.panels}
    expected = {
        FixPanel,
        PseudospectrumPanel,
        BearingsPanel,
        ResidualsPanel,
        ClassificationOverlayPanel,
        NullSteeringPanel,
    }
    assert panel_classes == expected
    assert layout.grid_shape == (4, 2)


def test_demo_layout_debug_has_all_ten_panels() -> None:
    """DEMO_LAYOUT_DEBUG renders §2.3 panels plus G7-extend BearingScanPanel."""
    layout = DEMO_LAYOUT_DEBUG
    panel_classes = {spec.panel_cls for spec in layout.panels}
    expected_panels = {
        FixPanel,
        BearingsPanel,
        GdopHeatmapPanel,
        PseudospectrumPanel,
        L1vsL2Panel,
        ResidualsPanel,
        NodeStatusPanel,
        ClassificationOverlayPanel,
        NullSteeringPanel,
        BearingScanPanel,
    }
    assert panel_classes == expected_panels
    assert layout.grid_shape == (4, 3)


def test_demo_layout_debug_bearing_scan_uses_polar_projection() -> None:
    """BearingScanPanel in DEBUG_LAYOUT carries projection='polar' subplot_kwarg.

    The panel renders RSSI(theta) as a polar plot; with default rectilinear
    Axes the matplotlib polar-specific calls (set_theta_zero_location,
    set_theta_direction) are silently skipped via hasattr guards, but the
    bearing-scan rendering is then misleading. This regression test pins
    the polar projection in the layout.
    """
    layout = DEMO_LAYOUT_DEBUG
    bearing_scan_specs = [spec for spec in layout.panels if spec.panel_cls is BearingScanPanel]
    assert len(bearing_scan_specs) == 1
    spec = bearing_scan_specs[0]
    assert spec.subplot_kwargs.get("projection") == "polar"


def test_demo_layout_minimal_is_fix_only() -> None:
    """DEMO_LAYOUT_MINIMAL ships only the FixPanel."""
    layout = DEMO_LAYOUT_MINIMAL
    panel_classes = [spec.panel_cls for spec in layout.panels]
    assert panel_classes == [FixPanel]
    assert layout.grid_shape == (1, 1)


def test_all_layouts_use_valid_panel_subclasses() -> None:
    """Every PanelSpec in every built-in layout names a real Panel subclass."""
    for layout in (DEMO_LAYOUT_TRENCH, DEMO_LAYOUT_DEBUG, DEMO_LAYOUT_MINIMAL):
        for spec in layout.panels:
            assert issubclass(spec.panel_cls, Panel), (
                f"{spec.panel_cls.__name__} is not a Panel subclass"
            )


def test_layout_integrity_rejects_duplicate_slot() -> None:
    """A layout with two panels in the same cell must be rejected."""
    bad_layout = DashboardLayout(
        panels=(
            PanelSpec(panel_cls=FixPanel, subplot_kwargs={"row": 0, "col": 0}),
            PanelSpec(panel_cls=BearingsPanel, subplot_kwargs={"row": 0, "col": 0}),
        ),
        grid_shape=(2, 2),
    )
    with pytest.raises(ValueError, match="overlaps"):
        _check_no_overlap(bad_layout)


def test_layout_integrity_rejects_out_of_bounds() -> None:
    """A PanelSpec that overflows the grid bounds must be rejected."""
    bad_layout = DashboardLayout(
        panels=(PanelSpec(panel_cls=FixPanel, subplot_kwargs={"row": 5, "col": 5}),),
        grid_shape=(2, 2),
    )
    with pytest.raises(ValueError, match="out of grid bounds"):
        _check_no_overlap(bad_layout)
