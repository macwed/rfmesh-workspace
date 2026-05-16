"""rfmesh-sdr -- SDR hardware adapters and SyntheticReceiver.

Workstream A. Implements ``rfmesh_contracts.Receiver`` and
``rfmesh_contracts.CoherentReceiver`` for RTL-SDR V4, HackRF One,
bladeRF 2.0 micro, ADALM-Pluto+, and the simulator.

v0.1.0 ships the synthetic simulator: single-channel L1 (WS-A-001) and
coherent multi-channel L2 (WS-A-002). Hardware backends arrive in later
tickets.
"""

from __future__ import annotations

from .exceptions import (
    CalibrationFailedError,
    InvalidReadSizeError,
    MalformedIQFileError,
    ReceiverNotOpenError,
)
from .io import IQMetadata, IQReader, IQRecorder
from .simulator import (
    AntennaPattern,
    ArraySpec,
    Calibration,
    ChannelImpairments,
    ChannelModel,
    EmitterSpec,
    FreeSpaceChannel,
    SimulationScenario,
    SyntheticReceiver,
    SyntheticReceiverCapabilities,
)

__all__ = [
    "AntennaPattern",
    "ArraySpec",
    "Calibration",
    "CalibrationFailedError",
    "ChannelImpairments",
    "ChannelModel",
    "EmitterSpec",
    "FreeSpaceChannel",
    "IQMetadata",
    "IQReader",
    "IQRecorder",
    "InvalidReadSizeError",
    "MalformedIQFileError",
    "ReceiverNotOpenError",
    "SimulationScenario",
    "SyntheticReceiver",
    "SyntheticReceiverCapabilities",
]
