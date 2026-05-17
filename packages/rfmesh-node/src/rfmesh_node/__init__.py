"""``rfmesh-node`` -- the asyncio composition root for an rfmesh node.

Wires the contract Protocol-typed pieces -- ``Receiver`` /
``BearingEstimator`` / ``Bearer`` / ``Fuser`` / ``CotPublisher`` --
into a running service. This is the **one** rfmesh package that
imports from sibling packages by design (ADR-011 D1); every other
package depends only on ``rfmesh-contracts``.

Public surface:

* ``Node`` -- per-node asyncio service.
* ``FusionService`` -- fusion-server asyncio service.
* ``DashboardPubSub`` / ``InProcessSubscriber`` / ``WebSocketSubscriber``
  -- the publish channel ops dashboards subscribe to.
* ``WifiBearer`` / ``LoraBearer`` / ``BothBearer`` -- transport
  ``Bearer`` Protocol conformers.
* ``detect_active_capabilities`` / ``build_estimators`` -- the
  startup gate that intersects declared capabilities with the
  live receiver.
* ``NodeRuntimeError`` / ``CapabilityMismatchError`` /
  ``ReceiverNotAvailableError`` -- the runtime exception
  hierarchy.
* ``run_node_main`` / ``run_fusion_main`` -- the two CLI entry
  points (also registered under ``[project.scripts]``).
"""

from __future__ import annotations

from .bearer import BothBearer, LoraBearer, WifiBearer
from .capabilities import build_estimators, detect_active_capabilities
from .cli import run_fusion_main, run_node_main
from .dashboard_pubsub import (
    DashboardPubSub,
    DashboardSubscriber,
    InProcessSubscriber,
    WebSocketSubscriber,
)
from .fusion_service import FusionService
from .node import Node
from .runtime import (
    CapabilityMismatchError,
    NodeRuntimeError,
    ReceiverNotAvailableError,
)

__all__ = [
    "BothBearer",
    "CapabilityMismatchError",
    "DashboardPubSub",
    "DashboardSubscriber",
    "FusionService",
    "InProcessSubscriber",
    "LoraBearer",
    "Node",
    "NodeRuntimeError",
    "ReceiverNotAvailableError",
    "WebSocketSubscriber",
    "WifiBearer",
    "build_estimators",
    "detect_active_capabilities",
    "run_fusion_main",
    "run_node_main",
]
