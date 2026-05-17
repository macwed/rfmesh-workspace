"""Dashboard panels.

Each panel is a subclass of ``Panel`` (``panels.base``) that owns one
``matplotlib.axes.Axes`` and updates in-place when a contract message
arrives over the ``DashboardClient`` stream. Every panel here maps to
at least one BoTH3 architectural advantage (HANDOFF §0); the table
lives in ``docs/design/ops-architecture.md`` §2.3.
"""

from __future__ import annotations

from rfmesh_ops.panels.base import DashboardMessage, Panel
from rfmesh_ops.panels.bearings import BearingsPanel
from rfmesh_ops.panels.classification_overlay import ClassificationOverlayPanel
from rfmesh_ops.panels.fix import FixPanel
from rfmesh_ops.panels.gdop_heatmap import GdopHeatmapPanel
from rfmesh_ops.panels.l1_vs_l2 import L1vsL2Panel
from rfmesh_ops.panels.node_status import NodeStatusPanel
from rfmesh_ops.panels.null_steering import NullSteeringPanel
from rfmesh_ops.panels.pseudospectrum import PseudospectrumPanel
from rfmesh_ops.panels.residuals import ResidualsPanel

__all__ = [
    "BearingsPanel",
    "ClassificationOverlayPanel",
    "DashboardMessage",
    "FixPanel",
    "GdopHeatmapPanel",
    "L1vsL2Panel",
    "NodeStatusPanel",
    "NullSteeringPanel",
    "Panel",
    "PseudospectrumPanel",
    "ResidualsPanel",
]
