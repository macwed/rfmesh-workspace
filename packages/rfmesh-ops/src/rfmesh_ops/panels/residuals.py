"""ResidualsPanel -- per-node residuals + outlier highlight.

Advantage #6 (system self-diagnosis) surface: each contributing node's
post-fit angular residual is rendered as a coloured dot; an outlier
(absolute residual / sigma > 3, per ADR-005 §D3) is rendered **red**
with the ``node_id`` label.

Why we re-derive the outlier flag here instead of reading it from the
``FixEvent`` contract: the contract's ``FixEvent.residuals_deg`` is
``tuple[float, ...]`` only -- the ``is_outlier`` flag is internal to
``rfmesh_fusion.residuals.ResidualsResult`` and does not cross the wire.
The dashboard tracks each node's most recent ``BearingReport.
azimuth_sigma_deg`` from the streaming ``BearingReport`` messages and
applies the same ADR-005 §D3 rule (``|r_i| / sigma_i > 3``) to the
``FixEvent.residuals_deg`` entries. This is one of two honest paths;
the alternative (extending the contract with ``residuals_is_outlier``)
is a SCHEMA_VERSION bump and lives in a future ADR.
"""

from __future__ import annotations

from matplotlib.axes import Axes
from rfmesh_contracts import BearingReport, FixEvent

from rfmesh_ops.panels.base import DashboardMessage, Panel

_TITLE = "Residuals (deg)"
# ADR-005 §D3: |r_i| / sigma_i > 3 -> outlier (strict greater-than).
_OUTLIER_SIGMA_MULTIPLIER = 3.0
# Y-axis half-width: residuals are typically <= a few sigma; +/- 15 deg
# is a comfortable default that does not clip plausible outliers.
_RESIDUAL_PLOT_HALF_WIDTH_DEG = 15.0


class ResidualsPanel(Panel):
    """Bar / dot panel showing per-node residuals from the latest FixEvent.

    Reads BearingReport (to track each node's current sigma) and
    FixEvent (to render the residuals after a fix).
    """

    handled_message_types = (BearingReport, FixEvent)

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._sigma_by_node: dict[str, float] = {}
        self._current_fix: FixEvent | None = None
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("Node")
        self.ax.set_ylabel("Residual (deg)")
        self.ax.set_ylim(-_RESIDUAL_PLOT_HALF_WIDTH_DEG, _RESIDUAL_PLOT_HALF_WIDTH_DEG)
        self.ax.axhline(0.0, color="gray", linewidth=0.5)
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
            # Track this node's current sigma so we can apply the
            # ADR-005 §D3 outlier rule against future FixEvent residuals.
            self._sigma_by_node[msg.node_id] = msg.azimuth_sigma_deg
            return
        if isinstance(msg, FixEvent):
            self._current_fix = msg
            self._render()

    def _render(self) -> None:
        if self._current_fix is None:
            self._render_empty()
            return
        fix = self._current_fix
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("Node")
        self.ax.set_ylabel("Residual (deg)")
        self.ax.set_ylim(-_RESIDUAL_PLOT_HALF_WIDTH_DEG, _RESIDUAL_PLOT_HALF_WIDTH_DEG)
        self.ax.axhline(0.0, color="gray", linewidth=0.5)
        self.ax.grid(visible=True, alpha=0.3)
        contributing = fix.contributing_nodes
        residuals_deg = fix.residuals_deg
        if len(contributing) != len(residuals_deg):
            # Contract guarantees same length; defensive only.
            return
        x_positions = list(range(len(contributing)))
        for x, node_id, residual in zip(x_positions, contributing, residuals_deg, strict=True):
            sigma = self._sigma_by_node.get(node_id)
            is_outlier = (
                sigma is not None
                and sigma > 0.0
                and abs(residual) / sigma > _OUTLIER_SIGMA_MULTIPLIER
            )
            color = "red" if is_outlier else "blue"
            marker_size = 80 if is_outlier else 50
            self.ax.scatter([x], [residual], color=color, s=marker_size, zorder=3)
            # Sigma error bars (when sigma known) so the operator can
            # see the 1-sigma envelope.
            if sigma is not None and sigma > 0.0:
                self.ax.plot([x, x], [-sigma, sigma], color="lightgray", linewidth=1.0, zorder=1)
            if is_outlier:
                # Label the outlier node by id -- the dashboard
                # explicitly surfaces *which* node misbehaves.
                self.ax.annotate(
                    f"OUTLIER: {node_id}",
                    (x, residual),
                    xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=8,
                    color="red",
                    fontweight="bold",
                )
        self.ax.set_xticks(x_positions)
        self.ax.set_xticklabels(contributing, rotation=30, fontsize=7)
