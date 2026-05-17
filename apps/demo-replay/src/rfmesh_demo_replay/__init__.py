"""``apps/demo-replay`` -- multi-node demo orchestrator + recorded-IQ replay.

Public surface:

* :class:`Scenario`, :class:`ScenarioLoader` -- YAML loader producing a
  fully-validated app-internal scenario object.
* :class:`ReplayOrchestrator` -- in-process orchestrator that walks
  through the scenario's beats, drives per-node estimators, pumps
  bearings into ``rfmesh_node.FusionService``, and (optionally) fans
  fixes out via a ``DashboardPubSub``.
* :class:`ReplayRecorder` -- Phase-C bench-side capture tool that
  writes per-node ``.iqx`` + ``.json`` files into a session
  directory.
* :class:`ReplayMetadata` -- the on-disk sidecar shape (extends
  ``rfmesh_sdr.io.metadata.IQMetadata``).

Top-level scripts (registered in ``pyproject.toml``):

* ``rfmesh-demo-replay`` -> :func:`rfmesh_demo_replay.cli.run_replay.run_replay_main`
* ``rfmesh-demo-record`` -> :func:`rfmesh_demo_replay.cli.run_record.run_record_main`

See ``docs/design/ops-architecture.md`` §1.5 + §2.4 for the binding
design.
"""

from __future__ import annotations

from .metadata import ReplayMetadata
from .recorder import ReplayRecorder
from .replay import ReplayOrchestrator
from .scenario import (
    ChannelModelSpec,
    DemoBeatSpec,
    EmitterSpec,
    EnuOrigin,
    ExpectedFix,
    ExpectedOutcomes,
    FusionSpec,
    NodeReplaySpec,
    Scenario,
    ScenarioLoader,
)

__all__ = [
    "ChannelModelSpec",
    "DemoBeatSpec",
    "EmitterSpec",
    "EnuOrigin",
    "ExpectedFix",
    "ExpectedOutcomes",
    "FusionSpec",
    "NodeReplaySpec",
    "ReplayMetadata",
    "ReplayOrchestrator",
    "ReplayRecorder",
    "Scenario",
    "ScenarioLoader",
]
