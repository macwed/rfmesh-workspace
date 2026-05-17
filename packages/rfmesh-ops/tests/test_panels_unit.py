"""Unit tests for every Panel.

One test per panel covers: construction against a real Axes, update
with a canonical fixture, and the honesty cuts (FixPanel percentage to
1 decimal; NullSteeringPanel 20-dB display cap; ClassificationOverlayPanel
None-vs-UNKNOWN differentiation; PseudospectrumPanel skips bearings
without payload).
"""

from __future__ import annotations

import re
from unittest.mock import patch
from uuid import UUID

import numpy as np
import pytest
from matplotlib.axes import Axes
from rfmesh_contracts import (
    ArrayGeometry,
    BearingReport,
    Capability,
    ConfidenceLevel,
    EllipseENU,
    EmitterClass,
    FixEvent,
    GeodeticPosition,
    NodeStatus,
)
from rfmesh_ops.panels import (
    BearingsPanel,
    ClassificationOverlayPanel,
    FixPanel,
    GdopHeatmapPanel,
    L1vsL2Panel,
    NodeStatusPanel,
    NullSteeringPanel,
    PseudospectrumPanel,
    ResidualsPanel,
)

# Named constants for assertions so ruff PLR2004 ("magic value in
# comparison") does not flag the obvious thresholds.
_MIN_BEARING_LINES = 3  # ray + two wedge edges
_MIN_AZIMUTH_LABELS = 2  # one L1 + one L2 entry on the A/B panel
_EXPECTED_NULL_N_ELEMENTS = 2
_EXPECTED_NULL_SPACING_M = 0.164


# ---------- NodeStatusPanel -----------------------------------------------


def test_node_status_panel_constructs_and_updates(
    axes: Axes,
    sample_node_status: NodeStatus,
    sample_gnss_denied_status: NodeStatus,
) -> None:
    """NodeStatusPanel renders both healthy and GNSS-denied entries."""
    panel = NodeStatusPanel(axes)
    panel.update(sample_node_status)
    panel.update(sample_gnss_denied_status)
    # Inspect rendered text artists for the EW-indicator differentiation.
    texts = [t.get_text() for t in axes.texts]
    joined = "\n".join(texts)
    assert "GNSS lock" in joined
    assert "GNSS denied" in joined


# ---------- BearingsPanel -------------------------------------------------


def test_bearings_panel_constructs_and_updates(
    axes: Axes,
    sample_bearing_report: BearingReport,
) -> None:
    """BearingsPanel renders a bearing ray + sigma wedge for one node."""
    panel = BearingsPanel(axes)
    panel.update(sample_bearing_report)
    # Three lines drawn per bearing: ray + two wedge edges (plus the
    # node marker, also rendered as a 1-point line plot).
    assert len(axes.lines) >= _MIN_BEARING_LINES
    # Node id annotation present.
    texts = [t.get_text() for t in axes.texts]
    assert sample_bearing_report.node_id in texts


# ---------- FixPanel: honesty cut for the percentage display --------------


def test_fix_panel_percentage_is_one_decimal_place(
    axes: Axes,
    sample_bearing_report: BearingReport,
    sample_fix_event: FixEvent,
) -> None:
    """ADR-005 §D5(b) + ADR-009 §D5: %-of-range is rendered to 1 decimal place.

    Establish the operator view centre via a BearingReport (node
    position), then render the fix: the percentage must match
    ``\\d+\\.\\d%`` (exactly one decimal digit, percent sign).
    """
    panel = FixPanel(axes)
    panel.update(sample_bearing_report)
    panel.update(sample_fix_event)
    texts = [t.get_text() for t in axes.texts]
    joined = "\n".join(texts)
    one_decimal_pattern = re.compile(r"\d+\.\d%")
    matches = one_decimal_pattern.findall(joined)
    assert matches, (
        f"FixPanel must render the percentage to 1 decimal place (ADR-005 D5b); "
        f"saw texts: {joined!r}"
    )


def test_fix_panel_percentage_reads_na_when_no_origin(
    axes: Axes,
    sample_fix_event: FixEvent,
) -> None:
    """No node-position context yet -> percentage reads 'n/a' honestly.

    Better than a divide-by-zero or a magic constant.
    """
    panel = FixPanel(axes)
    panel.update(sample_fix_event)
    texts = [t.get_text() for t in axes.texts]
    joined = "\n".join(texts)
    assert "n/a" in joined


def test_fix_panel_band_text_no_hedging(
    axes: Axes,
    sample_fix_event: FixEvent,
) -> None:
    """ADR-009: MEDIUM band text is verbatim 'MEDIUM' -- no hedging."""
    panel = FixPanel(axes)
    panel.update(sample_fix_event)
    texts = "\n".join(t.get_text() for t in axes.texts)
    assert "MEDIUM" in texts
    # Hedging strings must NOT appear.
    assert "almost HIGH" not in texts
    assert "near HIGH" not in texts


# ---------- PseudospectrumPanel: skip bearings without payload ------------


def test_pseudospectrum_panel_skips_bearings_without_payload(
    axes: Axes,
    sample_bearing_report: BearingReport,
    sample_l2_bearing_report: BearingReport,
) -> None:
    """Bearings with raw_pseudospectrum=None do not produce a trace."""
    panel = PseudospectrumPanel(axes)
    # L1 bearing -- no payload. Should be a no-op for rendering state.
    panel.update(sample_bearing_report)
    assert len(panel._latest) == 0
    # L2 bearing with payload -- should populate state and draw a line.
    panel.update(sample_l2_bearing_report)
    assert len(panel._latest) == 1
    assert sample_l2_bearing_report.node_id in panel._latest


# ---------- ResidualsPanel ------------------------------------------------


def test_residuals_panel_flags_outlier(
    axes: Axes,
    sample_geodetic_position: GeodeticPosition,
    sample_fix_with_outlier: FixEvent,
) -> None:
    """Outlier (|r|/sigma > 3) is rendered with the node_id label."""
    panel = ResidualsPanel(axes)
    # Track the per-node sigma from a stream of BearingReports first,
    # so the panel can compute the outlier flag from FixEvent.residuals_deg.
    for node_id in sample_fix_with_outlier.contributing_nodes:
        panel.update(
            BearingReport(
                node_id=node_id,
                t_unix_ns=1_000_000_000_000_000_000,
                node_position=sample_geodetic_position,
                azimuth_deg=45.0,
                azimuth_sigma_deg=1.0,  # so 8.5 / 1.0 = 8.5 -- well above 3
                method=Capability.L1_RSSI,
            )
        )
    panel.update(sample_fix_with_outlier)
    texts = [t.get_text() for t in axes.texts]
    joined = "\n".join(texts)
    # The outlier (node-rtl-03 with residual -8.5) must be labelled by id.
    assert "OUTLIER" in joined
    assert "node-rtl-03" in joined


# ---------- L1vsL2Panel ---------------------------------------------------


def test_l1_vs_l2_panel_renders_both(
    axes: Axes,
    sample_bearing_report: BearingReport,
    sample_l2_bearing_report: BearingReport,
) -> None:
    """Side-by-side panel renders both L1 and L2 entries."""
    panel = L1vsL2Panel(axes)
    panel.update(sample_bearing_report)  # L1
    panel.update(sample_l2_bearing_report)  # L2
    texts = "\n".join(t.get_text() for t in axes.texts)
    # Look for the L1 / L2 numeric labels.
    assert "+/-" in texts
    # Both bearings render labels with their azimuth values.
    matches = re.findall(r"\d+\.\d deg", texts)
    assert len(matches) >= _MIN_AZIMUTH_LABELS


# ---------- GdopHeatmapPanel ----------------------------------------------


def test_gdop_heatmap_panel_renders(
    axes: Axes,
    sample_node_status: NodeStatus,
    sample_gnss_denied_status: NodeStatus,
) -> None:
    """GdopHeatmapPanel renders after >=2 nodes report positions."""
    panel = GdopHeatmapPanel(axes)
    panel.update(sample_node_status)
    panel.update(sample_gnss_denied_status)
    # An imshow image must be present.
    assert len(axes.images) >= 1


# ---------- NullSteeringPanel: 20 dB display cap --------------------------


def test_null_steering_panel_caps_depth_at_20_db(axes: Axes) -> None:
    """ADR-008 §D8: claimed depth >20 dB renders as '>=20 dB' in UI text."""
    panel = NullSteeringPanel(axes)
    # Mock compute_receive_pattern so the test does not depend on the
    # full DSP module's behaviour: feed an azimuths grid and a gain_db
    # grid with a 40 dB peak-to-null delta -- well above the cap.
    azimuths_deg = np.arange(0.0, 360.0, 0.5, dtype=np.float64)
    gain_db = np.full_like(azimuths_deg, 0.0)
    gain_db[90] = -40.0  # 40 dB null at azimuth index 90 -> 45 deg
    weights = np.ones(2, dtype=np.complex64)
    with patch(
        "rfmesh_ops.panels.null_steering.compute_receive_pattern",
        return_value=(azimuths_deg, gain_db),
    ) as mock_compute:
        panel.set_pattern(
            weights,
            array_geometry=ArrayGeometry.ULA,
            n_elements=_EXPECTED_NULL_N_ELEMENTS,
            element_spacing_m=_EXPECTED_NULL_SPACING_M,
        )
        # ADR-010 API: compute_receive_pattern is called with the right kwargs.
        assert mock_compute.called
        _, kwargs = mock_compute.call_args
        assert kwargs["array_geometry"] is ArrayGeometry.ULA
        assert kwargs["n_elements"] == _EXPECTED_NULL_N_ELEMENTS
        assert kwargs["element_spacing_m"] == pytest.approx(_EXPECTED_NULL_SPACING_M)
    texts = "\n".join(t.get_text() for t in axes.texts)
    # Display must clamp at 20 dB.
    assert ">=20 dB" in texts, f"Expected '>=20 dB' display cap (ADR-008 D8); saw: {texts!r}"


def test_null_steering_panel_under_cap_renders_raw_value(axes: Axes) -> None:
    """Computed depth <= 20 dB renders the actual number to one decimal."""
    panel = NullSteeringPanel(axes)
    azimuths_deg = np.arange(0.0, 360.0, 0.5, dtype=np.float64)
    gain_db = np.full_like(azimuths_deg, 0.0)
    gain_db[90] = -18.0  # 18 dB depth -- within rehearsed band
    weights = np.ones(2, dtype=np.complex64)
    with patch(
        "rfmesh_ops.panels.null_steering.compute_receive_pattern",
        return_value=(azimuths_deg, gain_db),
    ):
        panel.set_pattern(
            weights,
            array_geometry=ArrayGeometry.ULA,
            n_elements=_EXPECTED_NULL_N_ELEMENTS,
            element_spacing_m=_EXPECTED_NULL_SPACING_M,
        )
    texts = "\n".join(t.get_text() for t in axes.texts)
    # 18.0 dB rendered to one decimal place.
    assert "18.0 dB" in texts


# ---------- ClassificationOverlayPanel: None vs UNKNOWN ------------------


def test_classification_overlay_panel_unknown_none_path(
    axes: Axes,
    sample_emitter_position: GeodeticPosition,
) -> None:
    """emitter_class=None -> 'UNKNOWN' label + 'no L3 capability' tooltip."""
    fix = _build_fix(sample_emitter_position, emitter_class=None)
    panel = ClassificationOverlayPanel(axes)
    panel.update(fix)
    texts = [t.get_text() for t in axes.texts]
    assert "UNKNOWN" in texts
    assert "no L3 capability" in texts


def test_classification_overlay_panel_unknown_emitter_path(
    axes: Axes,
    sample_emitter_position: GeodeticPosition,
) -> None:
    """emitter_class=UNKNOWN -> 'UNKNOWN' label + 'classifier ran, unsure' tooltip."""
    fix = _build_fix(sample_emitter_position, emitter_class=EmitterClass.UNKNOWN)
    panel = ClassificationOverlayPanel(axes)
    panel.update(fix)
    texts = [t.get_text() for t in axes.texts]
    assert "UNKNOWN" in texts
    assert "classifier ran, unsure" in texts


def test_classification_overlay_panel_known_class_path(
    axes: Axes,
    sample_emitter_position: GeodeticPosition,
) -> None:
    """A real EmitterClass renders its value upper-cased."""
    fix = _build_fix(sample_emitter_position, emitter_class=EmitterClass.ELRS)
    panel = ClassificationOverlayPanel(axes)
    panel.update(fix)
    texts = [t.get_text() for t in axes.texts]
    assert "ELRS" in texts
    # Confirm tooltip is NOT the no-L3 one (we have a real class).
    assert "no L3 capability" not in texts
    assert "classifier ran, unsure" not in texts


def _build_fix(
    emitter_position: GeodeticPosition,
    *,
    emitter_class: EmitterClass | None,
) -> FixEvent:
    return FixEvent(
        fix_id=UUID("abcdabcd-abcd-abcd-abcd-abcdabcdabcd"),
        t_unix_ns=1_700_000_000_000_000_000,
        position=emitter_position,
        covariance_m2=(100.0**2, 0.0, 80.0**2),
        confidence_ellipse_95=EllipseENU(
            semi_major_m=250.0,
            semi_minor_m=200.0,
            orientation_deg=0.0,
        ),
        confidence_level=ConfidenceLevel.MEDIUM,
        contributing_nodes=("node-a", "node-b", "node-c"),
        residuals_deg=(0.0, 0.0, 0.0),
        gdop=1.5,
        method="stansfield+mle",
        emitter_class=emitter_class,
    )
