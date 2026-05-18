"""BearingScanPanel -- live L1 sweep RSSI-vs-azimuth polar diagnostic.

G7 from the 2026-05-18 project audit + Phase C bench experience.

WHAT THIS RENDERS
-----------------
A polar (matplotlib `projection="polar"`) plot of RSSI (dB) vs heading
(deg) for the most recent L1 sweep on one node. The 6 dB prominence
threshold above the median noise-floor is drawn as a dashed ring; if
the sweep's peak rises above that ring the L1 estimator will emit a
bearing, otherwise it refuses. The shape an RF/EW expert recognises
in two seconds — a clean lobe (Mast C), a flat disk (Mast A), a
wrong-quadrant peak (Mast B).

Pairs with E1 (`L1AmplitudeSweepEstimator.last_refusal_reason`): when
the operator sees a flat disk on this panel, the caption surfaces the
refusal text from the estimator.

WHY OPERATOR-ACTION-DRIVEN, NOT STREAM-DRIVEN
---------------------------------------------
L1 sweep state is internal to the estimator; no wire-level message
carries the per-heading RSSI array. (A future wire-level surface
would need a SCHEMA_VERSION 1.2.0 bump per G3/G4 in the backlog.)
For v1.0 the orchestrator (apps/demo-replay) or the rfmesh-node
estimator-loop integration calls ``set_sweep()`` directly with the
heading + RSSI arrays it already has in scope at sweep finalisation.

The pattern mirrors NullSteeringPanel: a panel populated by operator
or orchestrator action, not by the streaming pubsub. Tests construct
the panel + call ``set_sweep`` directly.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from matplotlib.axes import Axes

from rfmesh_ops.panels.base import DashboardMessage, Panel

_TITLE = "L1 sweep diagnostic"
# Default prominence gate matches L1AmplitudeSweepEstimator's default
# (peak_prominence_db_min=6.0). Override per-sweep via set_sweep kwarg.
_DEFAULT_PROMINENCE_GATE_DB = 6.0
# Floor-percentile excluded when computing the median noise-floor;
# mirrors rfmesh-dsp.l1._NOISE_FLOOR_EXCLUDE_STRONGEST_PCT semantics
# (operator-side approximation; the estimator's exact floor is what
# the gate compares against — we render a visual estimate here).
_FLOOR_EXCLUDE_STRONGEST_FRAC = 0.2


class BearingScanPanel(Panel):
    """Polar RSSI-vs-heading diagnostic for an L1 sweep.

    State: zero or one sweep at a time. ``set_sweep`` fully replaces
    the rendered state; ``update(msg)`` is a no-op (operator-driven).
    """

    handled_message_types: tuple[type[DashboardMessage], ...] = ()

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._headings_deg: npt.NDArray[np.float64] | None = None
        self._rssi_dbfs: npt.NDArray[np.float64] | None = None
        self._node_id: str | None = None
        self._prominence_gate_db: float = _DEFAULT_PROMINENCE_GATE_DB
        self._refusal_reason: str | None = None
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        # Polar conventions per INTERFACES.md §0: 0 deg = north, CW positive.
        # PolarAxes has these methods; the type checker sees the generic
        # Axes superclass which does not, hence the targeted ignores.
        if hasattr(self.ax, "set_theta_zero_location"):
            self.ax.set_theta_zero_location("N")
            self.ax.set_theta_direction(-1)  # type: ignore[attr-defined]
        self.ax.text(
            0.5,
            0.5,
            "awaiting L1 sweep",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="gray",
        )

    def set_sweep(
        self,
        *,
        headings_deg: npt.NDArray[np.float64],
        rssi_dbfs: npt.NDArray[np.float64],
        node_id: str,
        prominence_gate_db: float = _DEFAULT_PROMINENCE_GATE_DB,
        refusal_reason: str | None = None,
    ) -> None:
        """Render a single L1 sweep's RSSI-vs-azimuth diagnostic.

        Args:
            headings_deg: 1-D array of antenna headings (deg, true-N=0,
                CW positive, range [0, 360)). Length must match
                ``rssi_dbfs``.
            rssi_dbfs: 1-D array of per-heading RSSI in dBFS (the same
                values the L1 estimator's `_fit_peak` receives).
            node_id: ID of the producing node for the title annotation.
            prominence_gate_db: The estimator's
                `peak_prominence_db_min` value — drawn as a dashed
                ring at `floor + gate` so the operator sees whether the
                peak rises above the bearing-emission threshold.
            refusal_reason: If the estimator refused (None bearing), the
                reason string from `last_refusal_reason` (E1). Rendered
                as caption text in orange.

        Raises:
            ValueError: If `headings_deg` and `rssi_dbfs` mismatch shape
                or are empty.
        """
        headings = np.asarray(headings_deg, dtype=np.float64)
        rssi = np.asarray(rssi_dbfs, dtype=np.float64)
        if headings.shape != rssi.shape:
            msg = (
                f"BearingScanPanel.set_sweep: shape mismatch "
                f"headings {headings.shape} vs rssi {rssi.shape}."
            )
            raise ValueError(msg)
        if headings.size == 0:
            msg = "BearingScanPanel.set_sweep: empty sweep."
            raise ValueError(msg)
        self._headings_deg = headings
        self._rssi_dbfs = rssi
        self._node_id = node_id
        self._prominence_gate_db = float(prominence_gate_db)
        self._refusal_reason = refusal_reason
        self._render()

    def update(self, msg: DashboardMessage) -> None:
        # Operator / orchestrator action driven; ignore streamed messages.
        del msg

    @property
    def node_id(self) -> str | None:
        """Stash of the most recent ``set_sweep`` call's `node_id` (test access)."""
        return self._node_id

    @property
    def has_sweep(self) -> bool:
        """True iff a sweep has been rendered (test access)."""
        return self._headings_deg is not None and self._rssi_dbfs is not None

    def _render(self) -> None:
        if self._headings_deg is None or self._rssi_dbfs is None:
            self._render_empty()
            return
        headings = self._headings_deg
        rssi = self._rssi_dbfs

        self.ax.clear()
        # Polar conventions.
        if hasattr(self.ax, "set_theta_zero_location"):
            self.ax.set_theta_zero_location("N")
            self.ax.set_theta_direction(-1)  # type: ignore[attr-defined]

        theta = np.deg2rad(headings)
        self.ax.plot(theta, rssi, "o-", linewidth=1.5, markersize=4)

        # Median-floor estimate (excluding the strongest 20 % of samples
        # — same idea as the estimator's compute_noise_floor_dbfs).
        sorted_rssi = np.sort(rssi)
        keep_n = max(1, int(np.ceil(rssi.size * (1.0 - _FLOOR_EXCLUDE_STRONGEST_FRAC))))
        floor_db = float(np.median(sorted_rssi[:keep_n]))
        gate_db = floor_db + self._prominence_gate_db

        # Draw the gate as a dashed ring at the gate radius.
        theta_ring = np.linspace(0.0, 2.0 * np.pi, 360)
        gate_ring = np.full_like(theta_ring, gate_db)
        self.ax.plot(theta_ring, gate_ring, "--", color="red", linewidth=1.2,
                     label=f"prominence gate ({self._prominence_gate_db:.1f} dB)")

        # Title + annotation.
        peak_db = float(np.max(rssi))
        prominence_actual_db = peak_db - floor_db
        passes_gate = prominence_actual_db >= self._prominence_gate_db
        verdict_text = (
            f"PASS ({prominence_actual_db:.1f} dB)"
            if passes_gate
            else f"REFUSED ({prominence_actual_db:.1f} dB < gate)"
        )
        node_label = self._node_id or "<unknown>"
        self.ax.set_title(f"{_TITLE} — {node_label}: {verdict_text}")

        # Refusal caption (orange) if present.
        if self._refusal_reason is not None:
            self.ax.text(
                0.5,
                -0.15,
                self._refusal_reason,
                ha="center",
                va="top",
                transform=self.ax.transAxes,
                color="darkorange",
                fontsize=8,
            )

        self.ax.legend(loc="lower left", fontsize=7, framealpha=0.8)
