"""NodeStatusPanel -- per-node health, gnss_locked, active capabilities.

Advantage #2 (sigma honesty + GNSS-denied) surface: every panel reading
``NodeStatus.gnss_locked == False`` is the system *observing* GNSS
jamming, not a degraded mode (see ``ARCHITECTURE.md`` §6 and
``docs/demo/script.md`` Q9).

Rendered as a small per-node table: ``node_id | healthy | gnss_locked |
active_capabilities | status_detail``.
"""

from __future__ import annotations

from matplotlib.axes import Axes
from rfmesh_contracts import NodeStatus

from rfmesh_ops.panels.base import DashboardMessage, Panel

_MAX_ROWS_DISPLAYED = 8
_TITLE = "Node Status"


class NodeStatusPanel(Panel):
    """Table-style panel showing the live mesh health.

    State is kept per-node (the most recent ``NodeStatus`` for each
    ``node_id`` is retained); calling ``update()`` with a new status
    for an existing node replaces that row.
    """

    handled_message_types = (NodeStatus,)

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._latest: dict[str, NodeStatus] = {}
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.axis("off")
        self.ax.text(
            0.5,
            0.5,
            "no nodes reporting",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="gray",
        )

    def update(self, msg: DashboardMessage) -> None:
        if not isinstance(msg, NodeStatus):
            return
        self._latest[msg.node_id] = msg
        self._render()

    def _render(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.axis("off")
        # Render up to _MAX_ROWS_DISPLAYED rows sorted by node_id.
        node_ids = sorted(self._latest)[:_MAX_ROWS_DISPLAYED]
        if not node_ids:
            self._render_empty()
            return
        # One row per node; columns: node_id, health, GNSS, capabilities.
        row_height = 1.0 / (len(node_ids) + 1)
        for idx, node_id in enumerate(node_ids):
            status = self._latest[node_id]
            y = 1.0 - (idx + 1) * row_height
            health_marker = "OK" if status.healthy else "FAIL"
            health_color = "green" if status.healthy else "red"
            # GNSS-denied EW indicator: red text when False so it is
            # readable as "we are observing GNSS denial" not "fault".
            gnss_text = "GNSS lock" if status.gnss_locked else "GNSS denied"
            gnss_color = "green" if status.gnss_locked else "orange"
            caps = ",".join(cap.value for cap in status.active_capabilities) or "-"
            self.ax.text(0.02, y, node_id, transform=self.ax.transAxes, fontsize=8)
            self.ax.text(
                0.30,
                y,
                health_marker,
                transform=self.ax.transAxes,
                fontsize=8,
                color=health_color,
            )
            self.ax.text(
                0.45,
                y,
                gnss_text,
                transform=self.ax.transAxes,
                fontsize=8,
                color=gnss_color,
            )
            self.ax.text(0.70, y, caps, transform=self.ax.transAxes, fontsize=7)
