"""rfmesh-contracts -- the frozen interface layer of the rfmesh project.

This package is the single source of truth that every other rfmesh workstream
depends on, and that no workstream may unilaterally change. It contains *only*
contract definitions -- data shapes (Pydantic models), behavioural shapes
(Protocols), enumerations, and the schema version -- with no runtime logic, no
I/O, and no dependency on any other rfmesh package.

THE DEPENDENCY RULE
-------------------
Every workstream package (``rfmesh-sdr``, ``rfmesh-dsp``, ``rfmesh-ml``,
``rfmesh-fusion``, ``rfmesh-cot``, ``rfmesh-node``, ``rfmesh-ops``) depends on
``rfmesh-contracts`` and communicates with every other workstream *only*
through the types defined here. No workstream depends on another workstream.
The dependency graph is a star with this package at the centre. That is what
lets many AI agents build the workstreams in parallel without their work
colliding -- the contracts are the contract.

GOVERNANCE
----------
Only the lead architect edits this package, and only via an accepted ADR (see
``docs/adr/``). Any change ends with a ``SCHEMA_VERSION`` bump in
``version.py``. A workstream agent who believes a contract must change writes
a CHANGE-REQUEST ADR and stops -- it does not edit the contract. See
``AGENTS.md`` -> "The Five Invariants".

WHAT IS EXPORTED
----------------
* ``SCHEMA_VERSION`` -- the frozen contract version.
* Enums -- ``Capability``, ``EmitterClass``, ``ConfidenceLevel``,
  ``ArrayGeometry``, ``BearerKind``.
* Geospatial value types -- ``GeodeticPosition``, ``EllipseENU``.
* Messages (the wire format) -- ``BearingReport``, ``FixEvent``,
  ``NodeStatus``.
* Config schemas -- ``SDRConfig``, ``ArrayConfig``, ``BearerConfig``,
  ``NodeConfig``, ``FusionConfig``, ``CommsConfig``.
* Protocols (behavioural contracts) -- ``Receiver``, ``CoherentReceiver``,
  ``ReceiverCapabilities``, ``Transmitter``, ``CoherentTransmitter``,
  ``TransmitterCapabilities``, ``BearingEstimator``, ``Fuser``,
  ``CotPublisher``, ``Bearer``, and the IQ type aliases
  ``IQBlock`` / ``CoherentIQBlock``.
"""

from __future__ import annotations

from .config import (
    ArrayConfig,
    BearerConfig,
    CommsConfig,
    FusionConfig,
    NodeConfig,
    SDRConfig,
)
from .enums import (
    ArrayGeometry,
    BearerKind,
    Capability,
    ConfidenceLevel,
    EmitterClass,
)
from .geospatial import EllipseENU, GeodeticPosition
from .messages import BearingReport, FixEvent, NodeStatus
from .protocols import (
    Bearer,
    BearingEstimator,
    CoherentIQBlock,
    CoherentReceiver,
    CoherentTransmitter,
    CotPublisher,
    Fuser,
    IQBlock,
    Receiver,
    ReceiverCapabilities,
    Transmitter,
    TransmitterCapabilities,
)
from .version import SCHEMA_VERSION

__all__ = [
    "SCHEMA_VERSION",
    "ArrayConfig",
    "ArrayGeometry",
    "Bearer",
    "BearerConfig",
    "BearerKind",
    "BearingEstimator",
    "BearingReport",
    "Capability",
    "CoherentIQBlock",
    "CoherentReceiver",
    "CoherentTransmitter",
    "CommsConfig",
    "ConfidenceLevel",
    "CotPublisher",
    "EllipseENU",
    "EmitterClass",
    "FixEvent",
    "Fuser",
    "FusionConfig",
    "GeodeticPosition",
    "IQBlock",
    "NodeConfig",
    "NodeStatus",
    "Receiver",
    "ReceiverCapabilities",
    "SDRConfig",
    "Transmitter",
    "TransmitterCapabilities",
]
