"""BearingsPanel -- 2-D ENU plot of each node's bearing line + sigma wedge.

Advantage #2 (sigma honesty) surface: each bearing renders as a ray
from the node position, with a triangular wedge whose half-angle is
``BearingReport.azimuth_sigma_deg``. The wedge *is* the honesty payload
the demo narrative leans on (script.md Q1, §2 Beat A).

The ENU origin is local to the dashboard view (default 0, 0). Node
positions are converted from geodetic to a local tangent plane using
an equirectangular small-region projection centred on the first node
seen. This panel does not require geodetic accuracy -- it is a *visual*
panel; the fusion solver is the authoritative geometric engine.
"""

from __future__ import annotations

import math

from matplotlib.axes import Axes
from rfmesh_contracts import BearingReport, FixEvent
from rfmesh_contracts.enums import Capability

from rfmesh_ops.panels.base import DashboardMessage, Panel

_BEARING_RAY_LENGTH_M = 3000.0
_TITLE = "Bearings"
_PLOT_HALF_WIDTH_M = 4000.0
_EARTH_RADIUS_M = 6_378_137.0


class BearingsPanel(Panel):
    """ENU-plane bearing lines with sigma wedges.

    State is one ``BearingReport`` per node (the most recent overwrites
    older ones). The plot is redrawn on every update.
    """

    handled_message_types = (BearingReport, FixEvent)

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._latest: dict[str, BearingReport] = {}
        # ENU origin (lat, lon) is set lazily from the first report.
        self._origin_lat_deg: float | None = None
        self._origin_lon_deg: float | None = None
        # Latest FixEvent for the red-X fix marker (demo-integrity R7).
        # The intersection of the bearing rays is what the operator's
        # eye traces -- overlaying the actual fix position lets the
        # jury confirm the geometry visually.
        self._latest_fix: FixEvent | None = None
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

    def update(self, msg: DashboardMessage) -> None:
        if isinstance(msg, FixEvent):
            # FixEvent on its own does not set the ENU origin (the
            # bearing reports establish it); we just remember the most
            # recent fix and re-render so the red X moves with it.
            self._latest_fix = msg
            self._render()
            return
        if not isinstance(msg, BearingReport):
            return
        if self._origin_lat_deg is None:
            self._origin_lat_deg = msg.node_position.lat_deg
            self._origin_lon_deg = msg.node_position.lon_deg
        self._latest[msg.node_id] = msg
        self._render()

    def _geodetic_to_enu_m(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        """Project (lat, lon) into local ENU (east, north) metres.

        Equirectangular small-region projection centred on the origin
        captured on the first ``update`` call. Accurate to ~cm at the
        ranges the dashboard plots (a few km).
        """
        if self._origin_lat_deg is None or self._origin_lon_deg is None:
            return (0.0, 0.0)
        lat_rad = math.radians(self._origin_lat_deg)
        d_lat_rad = math.radians(lat_deg - self._origin_lat_deg)
        d_lon_rad = math.radians(lon_deg - self._origin_lon_deg)
        east_m = _EARTH_RADIUS_M * d_lon_rad * math.cos(lat_rad)
        north_m = _EARTH_RADIUS_M * d_lat_rad
        return (east_m, north_m)

    def _render(self) -> None:
        self._render_empty()
        for node_id, report in sorted(self._latest.items()):
            east_m, north_m = self._geodetic_to_enu_m(
                report.node_position.lat_deg,
                report.node_position.lon_deg,
            )
            self._draw_bearing(east_m, north_m, report, node_id)
        # Fix marker: red X at the latest fix position (R7). Drawn last
        # so it lands on top of the bearing rays where they intersect.
        if self._latest_fix is not None and self._origin_lat_deg is not None:
            fix_east_m, fix_north_m = self._geodetic_to_enu_m(
                self._latest_fix.position.lat_deg,
                self._latest_fix.position.lon_deg,
            )
            self.ax.plot(
                [fix_east_m],
                [fix_north_m],
                marker="x",
                color="red",
                markersize=10,
                markeredgewidth=2,
            )

    def _draw_bearing(
        self,
        east_m: float,
        north_m: float,
        report: BearingReport,
        node_id: str,
    ) -> None:
        """Draw one node's bearing ray + sigma wedge.

        L1 refusal events (method = L1_REFUSED_PROMINENCE, ADR-013
        G4) draw a red X over the node marker plus an inline reason
        label instead of a bearing ray + wedge -- the sentinel
        azimuth / sigma values on a refusal report are not real
        directions and rendering them as a wedge would mislead the
        operator.
        """
        if report.method is Capability.L1_REFUSED_PROMINENCE:
            self._draw_refusal(east_m, north_m, report, node_id)
            return
        # Azimuth: true north = 0, clockwise positive. Translate to
        # ENU plotting frame: east = sin(az), north = cos(az).
        az_rad = math.radians(report.azimuth_deg)
        dx = _BEARING_RAY_LENGTH_M * math.sin(az_rad)
        dy = _BEARING_RAY_LENGTH_M * math.cos(az_rad)
        # Node position marker.
        self.ax.plot([east_m], [north_m], marker="o", color="blue", markersize=4)
        self.ax.annotate(
            node_id,
            (east_m, north_m),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=7,
        )
        # Bearing ray.
        self.ax.plot(
            [east_m, east_m + dx],
            [north_m, north_m + dy],
            color="blue",
            linewidth=1.0,
        )
        # Sigma wedge: triangle bounded by az +/- sigma rays.
        sigma_rad = math.radians(report.azimuth_sigma_deg)
        for sign in (-1.0, 1.0):
            edge_az = az_rad + sign * sigma_rad
            edge_dx = _BEARING_RAY_LENGTH_M * math.sin(edge_az)
            edge_dy = _BEARING_RAY_LENGTH_M * math.cos(edge_az)
            self.ax.plot(
                [east_m, east_m + edge_dx],
                [north_m, north_m + edge_dy],
                color="blue",
                linewidth=0.5,
                alpha=0.4,
                linestyle="--",
            )

    def _draw_refusal(
        self,
        east_m: float,
        north_m: float,
        report: BearingReport,
        node_id: str,
    ) -> None:
        """Render an L1 refusal event (no ray, red X + reason label)."""
        self.ax.plot(
            [east_m],
            [north_m],
            marker="x",
            color="red",
            markersize=8,
            markeredgewidth=2,
        )
        # demo-integrity rec on 53140df: 7pt is unreadable in field sun;
        # use a "REFUSED" badge (bold, 10pt) + the reason at a legible
        # 9pt below it so the jury reads the failure at 2 seconds.
        reason = report.refusal_reason or "L1 refused"
        self.ax.annotate(
            f"REFUSED  {node_id}",
            (east_m, north_m),
            xytext=(6, 8),
            textcoords="offset points",
            fontsize=10,
            color="red",
            fontweight="bold",
        )
        self.ax.annotate(
            reason,
            (east_m, north_m),
            xytext=(6, -8),
            textcoords="offset points",
            fontsize=9,
            color="red",
        )
