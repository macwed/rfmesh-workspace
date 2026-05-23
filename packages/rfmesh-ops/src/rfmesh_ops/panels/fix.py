"""FixPanel -- 95% ellipse, GDOP annotation, percentage display.

THIS PANEL IS LOAD-BEARING FOR DEMO HONESTY
-------------------------------------------
The percentage display (``100 * semi_major_m / range_m`` to **one
decimal place**) is binding per ADR-005 §D5(b) + ADR-009 §D5. Every
fix renders this number alongside the band name and the BoTH3 spec
reference (<= 1 %) as a shaded region. The percentage is what Maciej
quotes on stage (script.md §2 -- "Forty-nine percent of range" at Beat
B, "Twenty-six percent of range" at Beat C, etc.).

The confidence band text is the verbatim ``ConfidenceLevel.value``
(``HIGH``, ``MEDIUM``, ``LOW``) -- no hedging like "almost HIGH" per
ADR-009 narrative.

``range_m`` is the distance from the **operator view centre** (default
ENU origin at the first node seen on a BearingReport, or 0,0 if only
fixes have arrived) to ``fix.position``. The fusion solver picks its
own ENU origin internally; the dashboard's ``range_m`` is a separate,
operator-facing visual quantity. The ADR-009 narrative makes explicit
that the percentage can move *non-monotonically* when geometry improves
(see Beat D: percentage rises from 26% to 32% as the centroid shifts
toward an L2 node and ``range_m`` shrinks).
"""

from __future__ import annotations

import math

from matplotlib.axes import Axes
from matplotlib.patches import Ellipse
from rfmesh_contracts import BearingReport, ConfidenceLevel, FixEvent, NodeStatus

from rfmesh_ops.panels.base import DashboardMessage, Panel

_TITLE = "Fix"
_PLOT_HALF_WIDTH_M = 4000.0
_EARTH_RADIUS_M = 6_378_137.0
# BoTH3 jury spec band: 20 m at 2-5 km is roughly <= 1% of range.
# Shown as a shaded reference region so the operator sees where the
# competition tolerance lies independent of our HIGH/MEDIUM/LOW band.
_SPEC_BAND_FRACTION = 0.01
# Decimal places for the percentage display -- ADR-005 D5(b) binding.
_PERCENTAGE_DECIMALS = 1


class FixPanel(Panel):
    """The headline panel: ellipse + band + percentage + GDOP + spec band.

    Carries the demo's narrative load. The ellipse shrinks (or stretches
    into a slot when an L2 node joins, per Beat D) as nodes are added;
    the percentage is updated to 1 decimal place; the spec band (<=1%)
    is always visible as a shaded reference.

    The panel maintains a single "current fix" and renders it; if no
    fix has arrived yet, an empty axes is shown with the spec band
    reference circle.
    """

    # FixPanel also consumes BearingReport and NodeStatus messages to
    # establish the operator view centre (the first node position
    # seen). It does not RENDER those messages -- only the latest
    # FixEvent renders -- but they are needed so the percentage's
    # range_m has a meaningful non-zero denominator when the first
    # FixEvent arrives.
    handled_message_types = (FixEvent, BearingReport, NodeStatus)

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._current_fix: FixEvent | None = None
        # ENU origin = operator view centre. Lazy-initialised on the
        # first node-position-bearing message (BearingReport or
        # NodeStatus). Fallback (no nodes seen yet) is the fix
        # position itself -- but then range_m is zero and the
        # percentage display reads "n/a", honestly.
        self._origin_lat_deg: float | None = None
        self._origin_lon_deg: float | None = None
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("East (m)")
        self.ax.set_ylabel("North (m)")
        self.ax.set_xlim(-_PLOT_HALF_WIDTH_M, _PLOT_HALF_WIDTH_M)
        self.ax.set_ylim(-_PLOT_HALF_WIDTH_M, _PLOT_HALF_WIDTH_M)
        self.ax.set_aspect("equal")
        self.ax.grid(visible=True, alpha=0.3)
        self.ax.text(
            0.5,
            0.5,
            "awaiting fix",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="gray",
        )

    def update(self, msg: DashboardMessage) -> None:
        if isinstance(msg, BearingReport):
            # Track origin from the first node seen.
            if self._origin_lat_deg is None:
                self._origin_lat_deg = msg.node_position.lat_deg
                self._origin_lon_deg = msg.node_position.lon_deg
            return
        if isinstance(msg, NodeStatus):
            if self._origin_lat_deg is None:
                self._origin_lat_deg = msg.position.lat_deg
                self._origin_lon_deg = msg.position.lon_deg
            return
        if not isinstance(msg, FixEvent):
            return
        if self._origin_lat_deg is None:
            # No node positions yet -- fallback to the fix itself as
            # origin (range_m = 0 -> percentage reads "n/a", honest).
            self._origin_lat_deg = msg.position.lat_deg
            self._origin_lon_deg = msg.position.lon_deg
        self._current_fix = msg
        self._render()

    def _geodetic_to_enu_m(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        """Project (lat, lon) -> local ENU (east, north) metres."""
        if self._origin_lat_deg is None or self._origin_lon_deg is None:
            return (0.0, 0.0)
        lat_rad = math.radians(self._origin_lat_deg)
        d_lat_rad = math.radians(lat_deg - self._origin_lat_deg)
        d_lon_rad = math.radians(lon_deg - self._origin_lon_deg)
        east_m = _EARTH_RADIUS_M * d_lon_rad * math.cos(lat_rad)
        north_m = _EARTH_RADIUS_M * d_lat_rad
        return (east_m, north_m)

    def _render(self) -> None:
        if self._current_fix is None:
            self._render_empty()
            return
        fix = self._current_fix
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("East (m)")
        self.ax.set_ylabel("North (m)")
        self.ax.set_xlim(-_PLOT_HALF_WIDTH_M, _PLOT_HALF_WIDTH_M)
        self.ax.set_ylim(-_PLOT_HALF_WIDTH_M, _PLOT_HALF_WIDTH_M)
        self.ax.set_aspect("equal")
        self.ax.grid(visible=True, alpha=0.3)
        east_m, north_m = self._geodetic_to_enu_m(
            fix.position.lat_deg,
            fix.position.lon_deg,
        )
        self._draw_spec_band_reference(east_m, north_m)
        self._draw_ellipse(east_m, north_m, fix)
        # Centre marker.
        self.ax.plot([east_m], [north_m], marker="x", color="red", markersize=8)
        # Band + percentage + GDOP overlay (top-left corner of axes).
        self._draw_overlay(fix, east_m, north_m)

    def _draw_spec_band_reference(self, east_m: float, north_m: float) -> None:
        """Draw the BoTH3 spec band (<=1% of range) as a shaded ring."""
        range_m = self._range_to_fix_m(east_m, north_m)
        spec_radius_m = max(range_m * _SPEC_BAND_FRACTION, 1.0)
        spec_circle = Ellipse(
            xy=(east_m, north_m),
            width=2.0 * spec_radius_m,
            height=2.0 * spec_radius_m,
            angle=0.0,
            facecolor="green",
            alpha=0.15,
            edgecolor="green",
            linewidth=1.0,
            linestyle=":",
            label="BoTH3 spec band (<=1%)",
        )
        self.ax.add_patch(spec_circle)

    def _draw_ellipse(self, east_m: float, north_m: float, fix: FixEvent) -> None:
        """Draw the 95% confidence ellipse from FixEvent.confidence_ellipse_95."""
        ellipse_95 = fix.confidence_ellipse_95
        # EllipseENU.orientation_deg is the angle of the semi-major
        # axis from East toward North (ENU mathematical positive).
        # matplotlib's Ellipse `angle` is degrees CCW from x-axis -- so
        # they agree directly.
        ell = Ellipse(
            xy=(east_m, north_m),
            width=2.0 * ellipse_95.semi_major_m,
            height=2.0 * ellipse_95.semi_minor_m,
            angle=ellipse_95.orientation_deg,
            facecolor="none",
            edgecolor=self._band_color(fix.confidence_level),
            linewidth=2.0,
        )
        self.ax.add_patch(ell)

    def _band_color(self, level: ConfidenceLevel) -> str:
        if level is ConfidenceLevel.HIGH:
            return "green"
        if level is ConfidenceLevel.MEDIUM:
            return "yellow"
        return "red"

    def _range_to_fix_m(self, east_m: float, north_m: float) -> float:
        """Distance from operator view centre (0,0 ENU) to the fix."""
        return math.hypot(east_m, north_m)

    def _draw_overlay(self, fix: FixEvent, east_m: float, north_m: float) -> None:
        """Render the band + percentage + GDOP text overlay.

        Percentage is ``100 * semi_major_m / range_m`` to ONE decimal
        place. Binding per ADR-005 §D5(b) + ADR-009 §D5. Band text is
        the verbatim ``ConfidenceLevel.value`` (no hedging).
        """
        range_m = self._range_to_fix_m(east_m, north_m)
        semi_major_m = fix.confidence_ellipse_95.semi_major_m
        # Honest behaviour when range is zero (fix is at operator
        # centre): show "n/a" rather than a divide-by-zero or a magic
        # constant. The percentage display is meaningless without a
        # reference range.
        if range_m <= 0.0:
            percent_text = "n/a"
        else:
            pct = 100.0 * semi_major_m / range_m
            percent_text = f"{pct:.{_PERCENTAGE_DECIMALS}f}%"
        # Band text -- verbatim, no hedging (ADR-009).
        band_text = fix.confidence_level.value.upper()
        overlay_lines = [
            f"Band: {band_text}",
            f"%-of-range: {percent_text}",
            f"GDOP: {fix.gdop:.2f}",
            f"semi-major: {semi_major_m:.1f} m",
            f"method: {fix.method}",
        ]
        # Headline numbers (band / %-of-range / GDOP) are what Maciej
        # narrates during the demo; bumped to 11 pt so a projected
        # dashboard stays readable at distance (demo-integrity R2).
        self.ax.text(
            0.02,
            0.98,
            "\n".join(overlay_lines),
            transform=self.ax.transAxes,
            fontsize=11,
            verticalalignment="top",
            family="monospace",
            bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "gray"},
        )
