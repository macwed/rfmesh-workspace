"""NullSteeringPanel -- Advantage #4 dual-use receive-pattern A/B panel.

THE ONE SIBLING IMPORT
----------------------
This panel is the singular reason ``rfmesh-ops`` imports from
``rfmesh-dsp``. Per ADR-008 §D7 + ADR-010 + ADR-011 §D2, this panel
calls ``rfmesh_dsp.l2_null_steering.compute_receive_pattern(w,
array_geometry, n_elements, element_spacing_m, ...)`` and renders the
result as a polar plot.

THE 20 dB CLAIM CAP
-------------------
ADR-008 §D8 binds: **all UI text quoting null depth caps at 20 dB**.
If the computed depth exceeds 20 dB, the display reads ``>=20 dB`` --
never the raw number. Maciej's rehearsed script.md §3 Beat E.2 reads
"Eighteen decibels of jammer rejection" with the rehearsed band of
"15-20 dB typical, up to 25 dB with fresh calibration".

INPUTS
------
The panel does not consume a contract message stream directly --
null-steering is operator-action-triggered (the dashboard's "Engage
null" button calls ``set_pattern()`` with a recorded R + look vector).
For testing, the public ``set_pattern()`` method accepts the weight
vector and geometry directly. ``update(msg)`` is a no-op (the panel's
state is operator-set, not stream-driven).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from matplotlib.axes import Axes
from rfmesh_contracts import ArrayGeometry

# The one sibling import allowed for rfmesh-ops, per ADR-008 §D7 +
# ADR-010 + ADR-011 §D2. The `type: ignore` mirrors how the rest of
# the workspace handles cross-package imports that lack a py.typed
# marker in their producing package.
from rfmesh_dsp.l2_null_steering import (  # type: ignore[import-untyped, unused-ignore]
    compute_receive_pattern,
)

from rfmesh_ops.panels.base import DashboardMessage, Panel

_TITLE = "Null-steering (Advantage #4)"
# ADR-008 §D8: UI cap on quoted null depth, dB. Computed depth above
# this is rendered as ">=20 dB" in any caption text.
_DEPTH_CLAIM_CAP_DB = 20.0


class NullSteeringPanel(Panel):
    """Polar / linear plot of the array receive pattern.

    The dual-use story made operational. The panel renders nothing
    until ``set_pattern()`` is called with a weight vector + geometry;
    streaming messages do not affect it.
    """

    handled_message_types = ()  # operator-action-driven, not stream-driven

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._azimuths_deg: npt.NDArray[np.float64] | None = None
        self._gain_db: npt.NDArray[np.float64] | None = None
        self._claimed_depth_db: float | None = None
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("Azimuth (deg)")
        self.ax.set_ylabel("Receive gain (dB)")
        self.ax.set_xlim(0.0, 360.0)
        self.ax.grid(visible=True, alpha=0.3)
        self.ax.text(
            0.5,
            0.5,
            "press 'Engage null' to render pattern",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="gray",
        )

    def update(self, msg: DashboardMessage) -> None:
        # Operator-action-driven; streamed messages are ignored.
        del msg

    def set_pattern(
        self,
        weights: npt.NDArray[np.complex64],
        *,
        array_geometry: ArrayGeometry,
        n_elements: int,
        element_spacing_m: float,
        element_positions_m: npt.NDArray[np.float64] | None = None,
        frequency_hz: float = 915e6,
        scan_step_deg: float = 0.5,
        claimed_depth_db: float | None = None,
    ) -> None:
        """Recompute the receive pattern for new weights and render.

        Calls the rfmesh-dsp helper (ADR-010 API: geometry params are
        keyword) and stashes the result for the next ``_render()``.

        Args:
            weights: ``(N,)`` complex64 weight vector from
                ``compute_null_steering_weights``.
            array_geometry: ULA / UCA / CUSTOM.
            n_elements: Channel count; must match ``weights.shape[0]``.
            element_spacing_m: ULA spacing or UCA radius in metres.
            element_positions_m: Required for CUSTOM only.
            frequency_hz: Carrier frequency (Hz). Default 915 MHz.
            scan_step_deg: Angular resolution. Default 0.5 deg.
            claimed_depth_db: The depth value the operator wants the
                annotation to quote. Capped at 20 dB for display per
                ADR-008 §D8. ``None`` means "display the computed
                receive-pattern delta" (also capped).
        """
        azimuths_deg, gain_db = compute_receive_pattern(
            weights,
            array_geometry=array_geometry,
            n_elements=n_elements,
            element_spacing_m=element_spacing_m,
            element_positions_m=element_positions_m,
            frequency_hz=frequency_hz,
            scan_step_deg=scan_step_deg,
        )
        self._azimuths_deg = azimuths_deg
        self._gain_db = gain_db
        if claimed_depth_db is None:
            # Default: compute look:null delta from the pattern itself.
            self._claimed_depth_db = float(np.max(gain_db) - np.min(gain_db))
        else:
            self._claimed_depth_db = claimed_depth_db
        self._render()

    def _format_depth_text(self) -> str:
        """ADR-008 §D8 cap on quoted depth in all UI text."""
        if self._claimed_depth_db is None:
            return "depth: n/a"
        if self._claimed_depth_db > _DEPTH_CLAIM_CAP_DB:
            return f"depth: >={int(_DEPTH_CLAIM_CAP_DB)} dB"
        return f"depth: {self._claimed_depth_db:.1f} dB"

    def _render(self) -> None:
        if self._azimuths_deg is None or self._gain_db is None:
            self._render_empty()
            return
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("Azimuth (deg)")
        self.ax.set_ylabel("Receive gain (dB)")
        self.ax.set_xlim(0.0, 360.0)
        self.ax.grid(visible=True, alpha=0.3)
        self.ax.plot(self._azimuths_deg, self._gain_db, color="tab:blue", linewidth=1.0)
        depth_text = self._format_depth_text()
        self.ax.text(
            0.02,
            0.98,
            depth_text + "\nclaim cap: 20 dB (ADR-008 D8)",
            transform=self.ax.transAxes,
            fontsize=8,
            verticalalignment="top",
            family="monospace",
            bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "gray"},
        )
