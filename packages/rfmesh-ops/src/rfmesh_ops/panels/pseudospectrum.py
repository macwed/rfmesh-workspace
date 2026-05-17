"""PseudospectrumPanel -- P_MUSIC(theta) per L2 node.

Renders the optional ``BearingReport.raw_pseudospectrum`` payload --
720 little-endian float32 log-magnitude samples over [0, 360) degrees
at 0.5 deg step, per ``INTERFACES.md`` §3. Bearings without the payload
(L1, LoRa-borne L2) are silently skipped: the panel does not invent
data.

ARCHITECTURE.md §7 calls this out as "*the single piece of evidence
that subspace DF is actually running on coherent IQ*. Without this, an
expert in the room cannot distinguish L2 from 'we said L2'." That is
the audit obligation this panel honours.
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt
from matplotlib.axes import Axes
from rfmesh_contracts import BearingReport

from rfmesh_ops.panels.base import DashboardMessage, Panel

_LOG = logging.getLogger(__name__)

_TITLE = "MUSIC pseudospectrum"
# Per INTERFACES.md §3: little-endian float32 samples over [0, 360) at
# 0.5 deg step. 720 samples * 4 bytes = 2880 bytes is the conventional
# size; this constant is what we expect, but the panel does not enforce
# (it adapts to whatever 4-byte aligned size arrives, so future
# convention changes degrade gracefully).
_BYTES_PER_FLOAT32 = 4
_EXPECTED_SAMPLE_COUNT = 720  # = 360 / 0.5  (rf-dsp-council NOTE 5)


class PseudospectrumPanel(Panel):
    """Linear plot of P_MUSIC(theta) in dB over [0, 360) degrees.

    State is one pseudospectrum per node_id (the most recent overwrites
    older). Bearings without a payload are skipped (not erased) -- the
    last MUSIC trace stays on screen until a new L2 bearing arrives.
    """

    handled_message_types = (BearingReport,)

    def __init__(self, ax: Axes) -> None:
        super().__init__(ax)
        self._latest: dict[str, npt.NDArray[np.float32]] = {}
        self._warned_sizes: set[tuple[str, int]] = set()
        self._render_empty()

    def _render_empty(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("Azimuth (deg)")
        self.ax.set_ylabel("P_MUSIC (dB, log-mag)")
        self.ax.set_xlim(0.0, 360.0)
        self.ax.grid(visible=True, alpha=0.3)
        self.ax.text(
            0.5,
            0.5,
            "no L2 pseudospectrum yet",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="gray",
        )

    def update(self, msg: DashboardMessage) -> None:
        if not isinstance(msg, BearingReport):
            return
        # Skip bearings without the payload: L1 reports, LoRa-borne L2
        # reports (LoRa drops the payload for bandwidth -- INTERFACES.md
        # §3). Silently skip; the last trace stays on screen.
        if msg.raw_pseudospectrum is None or len(msg.raw_pseudospectrum) == 0:
            return
        # Decode little-endian float32 samples.
        if len(msg.raw_pseudospectrum) % _BYTES_PER_FLOAT32 != 0:
            return
        samples = np.frombuffer(msg.raw_pseudospectrum, dtype="<f4")
        if samples.size == 0:
            return
        # rf-dsp-council NOTE 5: a non-720-sample payload renders at the
        # implied step but warns once per (node_id, sample_count) so
        # malformed payloads are operator-visible in the log.
        if samples.size != _EXPECTED_SAMPLE_COUNT:
            key = (msg.node_id, samples.size)
            if key not in self._warned_sizes:
                _LOG.warning(
                    "PseudospectrumPanel: node %s shipped %d-sample pseudospectrum "
                    "(expected %d for 0.5 deg step over [0, 360)); rendering at "
                    "implied step. Per INTERFACES.md §3.",
                    msg.node_id,
                    samples.size,
                    _EXPECTED_SAMPLE_COUNT,
                )
                self._warned_sizes.add(key)
        self._latest[msg.node_id] = samples
        self._render()

    def _render(self) -> None:
        self.ax.clear()
        self.ax.set_title(_TITLE)
        self.ax.set_xlabel("Azimuth (deg)")
        self.ax.set_ylabel("P_MUSIC (dB, log-mag)")
        self.ax.set_xlim(0.0, 360.0)
        self.ax.grid(visible=True, alpha=0.3)
        for node_id, samples in sorted(self._latest.items()):
            azimuths_deg = np.linspace(0.0, 360.0, samples.size, endpoint=False)
            self.ax.plot(azimuths_deg, samples, label=node_id, linewidth=1.0)
        if self._latest:
            self.ax.legend(loc="upper right", fontsize=7)
