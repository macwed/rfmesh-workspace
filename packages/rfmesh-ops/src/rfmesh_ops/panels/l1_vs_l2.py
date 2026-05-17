"""L1vsL2Panel -- side-by-side L1 / L2 precision A/B comparison.

Advantage #3 (heterogeneous mesh) surface: same emitter, same instant,
one panel shows ``L1: az +/- sigma`` next to ``L2: az +/- sigma`` so
the jury sees the precision delta of subspace DF against amplitude
sweep without us narrating it.

Pairs the most recent L1_RSSI bearing with the most recent L2_MUSIC /
L2_CAPON bearing across the whole mesh (any L1 node + any L2 node).
This is operationally honest because for a *given emitter*, the two
methods land on the same azimuth modulo per-sensor sigma; the
side-by-side widget is the visible witness.

Hardware-validated scenario this panel narrates (script.md §2 Beat D
A/B caption): "L1: 8.2 deg +/- 3. L2: 1.4 deg +/- 0.6."
"""

from __future__ import annotations

from matplotlib.axes import Axes
from rfmesh_contracts import BearingReport, Capability

from rfmesh_ops.panels.base import DashboardMessage, Panel

_TITLE = "L1 vs L2"
_L2_METHODS: frozenset[Capability] = frozenset({Capability.L2_MUSIC, Capability.L2_CAPON})


class L1vsL2Panel(Panel):
    """Side-by-side L1 and L2 bearing display.

    Tracks the most recent L1 bearing and the most recent L2 bearing
    (regardless of node_id, on the assumption that the bearings refer
    to the same emitter window). Renders both with sigma error bars.
    """

    handled_message_types = (BearingReport,)

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._latest_l1: BearingReport | None = None
        self._latest_l2: BearingReport | None = None
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("Method")
        self.ax.set_ylabel("Azimuth (deg)")
        self.ax.set_ylim(0.0, 360.0)
        self.ax.grid(visible=True, alpha=0.3)
        self.ax.text(
            0.5,
            0.5,
            "awaiting L1 and L2 bearings",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="gray",
        )

    def update(self, msg: DashboardMessage) -> None:
        if not isinstance(msg, BearingReport):
            return
        if msg.method is Capability.L1_RSSI:
            self._latest_l1 = msg
        elif msg.method in _L2_METHODS:
            self._latest_l2 = msg
        self._render()

    def _render(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("Method")
        self.ax.set_ylabel("Azimuth (deg)")
        self.ax.set_ylim(0.0, 360.0)
        self.ax.grid(visible=True, alpha=0.3)
        positions: list[int] = []
        labels: list[str] = []
        if self._latest_l1 is not None:
            positions.append(0)
            labels.append(f"L1 (sigma={self._latest_l1.azimuth_sigma_deg:.2f} deg)")
            self.ax.errorbar(
                [0],
                [self._latest_l1.azimuth_deg],
                yerr=[self._latest_l1.azimuth_sigma_deg],
                fmt="o",
                color="tab:blue",
                capsize=8,
                markersize=10,
                label="L1",
            )
            self.ax.text(
                0.0,
                self._latest_l1.azimuth_deg + 10.0,
                (
                    f"{self._latest_l1.azimuth_deg:.1f} deg "
                    f"+/- {self._latest_l1.azimuth_sigma_deg:.2f}"
                ),
                ha="center",
                fontsize=8,
                color="tab:blue",
            )
        if self._latest_l2 is not None:
            positions.append(1)
            labels.append(f"L2 (sigma={self._latest_l2.azimuth_sigma_deg:.2f} deg)")
            self.ax.errorbar(
                [1],
                [self._latest_l2.azimuth_deg],
                yerr=[self._latest_l2.azimuth_sigma_deg],
                fmt="o",
                color="tab:red",
                capsize=8,
                markersize=10,
                label="L2",
            )
            self.ax.text(
                1.0,
                self._latest_l2.azimuth_deg + 10.0,
                (
                    f"{self._latest_l2.azimuth_deg:.1f} deg "
                    f"+/- {self._latest_l2.azimuth_sigma_deg:.2f}"
                ),
                ha="center",
                fontsize=8,
                color="tab:red",
            )
        if positions:
            self.ax.set_xticks(positions)
            self.ax.set_xticklabels(labels, fontsize=8)
            self.ax.set_xlim(-0.5, 1.5)
