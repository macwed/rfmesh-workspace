"""rfmesh-ops -- live ops dashboard and deployment tooling.

Workstream C+D. Live display of node status, bearings (with sigma
wedges), cross-fixes, GDOP heatmap, classification overlay, and the
Advantage #4 receive-pattern A/B panel.

The dashboard consumes ``BearingReport``, ``FixEvent``, and
``NodeStatus`` from ``rfmesh-contracts`` over a transport
(``DashboardClient`` -- WebSocket or in-process queue). The single
sibling import outside contracts is
``rfmesh_dsp.l2_null_steering.compute_receive_pattern`` (the
NullSteeringPanel -- ADR-008 §D7 + ADR-010 + ADR-011 §D2).

Public API:

* ``Dashboard`` -- the live application.
* ``DashboardClient`` -- transport-side message stream (factories:
  ``websocket(url)``, ``in_process(queue)``).
* ``DashboardLayout``, ``PanelSpec`` -- layout construction.
* Built-in layouts -- ``DEMO_LAYOUT_TRENCH``, ``DEMO_LAYOUT_DEBUG``,
  ``DEMO_LAYOUT_MINIMAL``.
* Panels -- see ``rfmesh_ops.panels``.
"""

from __future__ import annotations

from rfmesh_ops.client import DashboardClient
from rfmesh_ops.dashboard import Dashboard
from rfmesh_ops.layouts import (
    DEMO_LAYOUT_DEBUG,
    DEMO_LAYOUT_MINIMAL,
    DEMO_LAYOUT_TRENCH,
    LAYOUTS_BY_NAME,
    DashboardLayout,
    PanelSpec,
)
from rfmesh_ops.panels import (
    BearingsPanel,
    ClassificationOverlayPanel,
    FixPanel,
    GdopHeatmapPanel,
    L1vsL2Panel,
    NodeStatusPanel,
    NullSteeringPanel,
    Panel,
    PseudospectrumPanel,
    ResidualsPanel,
)

__all__ = [
    "DEMO_LAYOUT_DEBUG",
    "DEMO_LAYOUT_MINIMAL",
    "DEMO_LAYOUT_TRENCH",
    "LAYOUTS_BY_NAME",
    "BearingsPanel",
    "ClassificationOverlayPanel",
    "Dashboard",
    "DashboardClient",
    "DashboardLayout",
    "FixPanel",
    "GdopHeatmapPanel",
    "L1vsL2Panel",
    "NodeStatusPanel",
    "NullSteeringPanel",
    "Panel",
    "PanelSpec",
    "PseudospectrumPanel",
    "ResidualsPanel",
]
