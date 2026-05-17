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
    """DEMO_LAYOUT_TRENCH has the architect-mandated 5 panels in a 3x2 grid."""
    layout = DEMO_LAYOUT_TRENCH
    assert isinstance(layout, DashboardLayout)
    panel_classes = {spec.panel_cls for spec in layout.panels}
    expected = {
        FixPanel,
        BearingsPanel,
        ResidualsPanel,
        ClassificationOverlayPanel,
        NullSteeringPanel,
    }
    assert panel_classes == expected
    assert layout.grid_shape == (3, 2)


def test_demo_layout_debug_has_all_nine_panels() -> None:
    """DEMO_LAYOUT_DEBUG renders every panel the architect named in §2.3."""
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
    }
    assert panel_classes == expected_panels
    assert layout.grid_shape == (3, 3)


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
