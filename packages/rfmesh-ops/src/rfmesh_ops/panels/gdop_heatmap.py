"""GdopHeatmapPanel -- GDOP across a candidate emitter region.

Advantage #1 + #6 surface: a colormap of GDOP across the operational
area for the current node geometry. Tells the operator *where the
system is precise* and *where it is not*, independent of any specific
fix -- the engineering artefact that separates a demo from a working
system (ARCHITECTURE.md §7).

GDOP at a candidate emitter location (xe, ye) given node positions
(xi, yi) with sigma_i is the standard bearings-only DOP:

    H_ij = d/d_emitter [azimuth_i(emitter)] -> per-bearing geometric
    P = (H^T W H)^{-1}, with W = diag(1/sigma_i^2)
    GDOP = sqrt(trace(P))

This panel computes a coarse grid of GDOP values from the most recent
``NodeStatus`` positions and renders an imshow heatmap with the BoTH3
``gdop_warn_threshold`` (default 6.0) contour highlighted.

Note: the calculation is geometric (does not require a real fix). Sigma
is read from the most recent ``BearingReport.azimuth_sigma_deg`` per
node, falling back to a conservative default if unknown.
"""

from __future__ import annotations

import contextlib
import math

import numpy as np
import numpy.typing as npt
from matplotlib.axes import Axes
from rfmesh_contracts import BearingReport, NodeStatus

from rfmesh_ops.panels.base import DashboardMessage, Panel

_TITLE = "GDOP heatmap"
_GRID_HALF_WIDTH_M = 4000.0
_GRID_RESOLUTION = 40
_FALLBACK_SIGMA_DEG = 5.0
_GDOP_WARN_THRESHOLD = 6.0
_GDOP_DISPLAY_CLIP = 20.0
_EARTH_RADIUS_M = 6_378_137.0
_MIN_NODES_FOR_GDOP = 2


class GdopHeatmapPanel(Panel):
    """Heatmap of bearings-only GDOP over a fixed grid."""

    handled_message_types = (NodeStatus, BearingReport)

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._node_positions_enu: dict[str, tuple[float, float]] = {}
        self._sigma_by_node: dict[str, float] = {}
        self._origin_lat_deg: float | None = None
        self._origin_lon_deg: float | None = None
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("East (m)")
        self.ax.set_ylabel("North (m)")
        self.ax.set_xlim(-_GRID_HALF_WIDTH_M, _GRID_HALF_WIDTH_M)
        self.ax.set_ylim(-_GRID_HALF_WIDTH_M, _GRID_HALF_WIDTH_M)
        self.ax.set_aspect("equal")
        self.ax.grid(visible=True, alpha=0.3)
        self.ax.text(
            0.5,
            0.5,
            "awaiting >= 2 node positions",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="gray",
        )

    def update(self, msg: DashboardMessage) -> None:
        if isinstance(msg, BearingReport):
            self._sigma_by_node[msg.node_id] = msg.azimuth_sigma_deg
            return
        if not isinstance(msg, NodeStatus):
            return
        if self._origin_lat_deg is None:
            self._origin_lat_deg = msg.position.lat_deg
            self._origin_lon_deg = msg.position.lon_deg
        east_m, north_m = self._geodetic_to_enu_m(
            msg.position.lat_deg,
            msg.position.lon_deg,
        )
        self._node_positions_enu[msg.node_id] = (east_m, north_m)
        if len(self._node_positions_enu) >= _MIN_NODES_FOR_GDOP:
            self._render()

    def _geodetic_to_enu_m(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        if self._origin_lat_deg is None or self._origin_lon_deg is None:
            return (0.0, 0.0)
        lat_rad = math.radians(self._origin_lat_deg)
        d_lat_rad = math.radians(lat_deg - self._origin_lat_deg)
        d_lon_rad = math.radians(lon_deg - self._origin_lon_deg)
        east_m = _EARTH_RADIUS_M * d_lon_rad * math.cos(lat_rad)
        north_m = _EARTH_RADIUS_M * d_lat_rad
        return (east_m, north_m)

    def _compute_gdop_grid(self) -> npt.NDArray[np.float64]:
        """Compute GDOP over a fixed coarse grid in ENU metres."""
        x_axis = np.linspace(-_GRID_HALF_WIDTH_M, _GRID_HALF_WIDTH_M, _GRID_RESOLUTION)
        y_axis = np.linspace(-_GRID_HALF_WIDTH_M, _GRID_HALF_WIDTH_M, _GRID_RESOLUTION)
        gdop_grid = np.full((_GRID_RESOLUTION, _GRID_RESOLUTION), np.nan, dtype=np.float64)
        node_items = list(self._node_positions_enu.items())
        for j, ye in enumerate(y_axis):
            for i, xe in enumerate(x_axis):
                gdop_grid[j, i] = self._compute_gdop_at(xe, ye, node_items)
        return gdop_grid

    def _compute_gdop_at(
        self,
        emitter_x: float,
        emitter_y: float,
        node_items: list[tuple[str, tuple[float, float]]],
    ) -> float:
        """GDOP at one emitter location.

        Bearings-only DOP. The bearing residual gradient is
        d/d_emitter atan2(dx, dy); the Jacobian rows are
        [(dy/r^2), (-dx/r^2)] per node. Information matrix is
        H^T W H with W = diag(1/sigma_i_rad^2); GDOP = sqrt(trace(inv(I))).
        """
        h_rows: list[list[float]] = []
        weights: list[float] = []
        for node_id, (nx, ny) in node_items:
            dx = emitter_x - nx
            dy = emitter_y - ny
            r_sq = dx * dx + dy * dy
            if r_sq <= 0.0:
                continue
            # Geographic azimuth: az = atan2(dx, dy). d_az/d_emitter_x =
            # dy / r^2; d_az/d_emitter_y = -dx / r^2.
            h_rows.append([dy / r_sq, -dx / r_sq])
            sigma_deg = self._sigma_by_node.get(node_id, _FALLBACK_SIGMA_DEG)
            sigma_rad = math.radians(max(sigma_deg, 1e-3))
            weights.append(1.0 / (sigma_rad * sigma_rad))
        if len(h_rows) < _MIN_NODES_FOR_GDOP:
            return float("nan")
        h_matrix = np.array(h_rows, dtype=np.float64)
        w_diag = np.diag(weights)
        try:
            info = h_matrix.T @ w_diag @ h_matrix
            cov = np.linalg.inv(info)
        except np.linalg.LinAlgError:
            return float("nan")
        trace = float(np.trace(cov))
        if trace <= 0.0:
            return float("nan")
        return math.sqrt(trace)

    def _render(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("East (m)")
        self.ax.set_ylabel("North (m)")
        self.ax.set_xlim(-_GRID_HALF_WIDTH_M, _GRID_HALF_WIDTH_M)
        self.ax.set_ylim(-_GRID_HALF_WIDTH_M, _GRID_HALF_WIDTH_M)
        self.ax.set_aspect("equal")
        self.ax.grid(visible=True, alpha=0.3)
        gdop_grid = self._compute_gdop_grid()
        # Clip for display (warn threshold + saturation).
        display_grid = np.clip(gdop_grid, 0.0, _GDOP_DISPLAY_CLIP)
        im = self.ax.imshow(
            display_grid,
            extent=(
                -_GRID_HALF_WIDTH_M,
                _GRID_HALF_WIDTH_M,
                -_GRID_HALF_WIDTH_M,
                _GRID_HALF_WIDTH_M,
            ),
            origin="lower",
            aspect="auto",
            cmap="viridis_r",
            vmin=0.0,
            vmax=_GDOP_DISPLAY_CLIP,
        )
        # Drop a small colorbar tied to this axes. Colorbar can fail
        # under some test backends; the panel still works visually.
        with contextlib.suppress(ValueError, RuntimeError):
            self.ax.figure.colorbar(im, ax=self.ax, fraction=0.046, pad=0.04)
        # Plot node positions as markers.
        for node_id, (nx, ny) in self._node_positions_enu.items():
            self.ax.plot([nx], [ny], marker="^", color="white", markersize=6)
            self.ax.annotate(
                node_id,
                (nx, ny),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=7,
                color="white",
            )
        # Mark the warn threshold contour. Drawing the contour line (not
        # just the legend text) lets the operator SEE where the
        # favourable region ends -- the jury-readable artefact that
        # answers script.md Q3 visually (demo-integrity R3).
        # Contouring can fail under degenerate grids (e.g. all-NaN); the
        # legend text still tells the story when the contour cannot be
        # drawn.
        finite_mask = np.isfinite(gdop_grid)
        if finite_mask.any() and gdop_grid[finite_mask].min() < _GDOP_WARN_THRESHOLD:
            with contextlib.suppress(ValueError, RuntimeError):
                self.ax.contour(
                    np.linspace(-_GRID_HALF_WIDTH_M, _GRID_HALF_WIDTH_M, _GRID_RESOLUTION),
                    np.linspace(-_GRID_HALF_WIDTH_M, _GRID_HALF_WIDTH_M, _GRID_RESOLUTION),
                    gdop_grid,
                    levels=[_GDOP_WARN_THRESHOLD],
                    colors="white",
                    linewidths=1.2,
                    linestyles="--",
                )
        self.ax.text(
            0.02,
            0.98,
            f"GDOP warn threshold (white contour): {_GDOP_WARN_THRESHOLD:.1f}",
            transform=self.ax.transAxes,
            fontsize=7,
            verticalalignment="top",
            bbox={"facecolor": "white", "alpha": 0.6, "edgecolor": "gray"},
        )
