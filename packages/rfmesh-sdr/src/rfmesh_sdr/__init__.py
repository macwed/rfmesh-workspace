"""rfmesh-sdr -- SDR hardware adapters and SyntheticReceiver.

Workstream A. Implements ``rfmesh_contracts.Receiver`` and
``rfmesh_contracts.CoherentReceiver`` for RTL-SDR V4, HackRF One,
bladeRF 2.0 micro, ADALM-Pluto+, and the simulator.

v0.1.0 ships the synthetic simulator: single-channel L1 (WS-A-001),
coherent multi-channel L2 (WS-A-002), and channel / receiver
impairments (WS-A-003). Hardware backends arrive in later tickets.
"""

from __future__ import annotations

from .devices import RTLSDRDevice, RTLSDRDeviceCapabilities
from .exceptions import (
    CalibrationFailedError,
    HardwareError,
    InvalidReadSizeError,
    MalformedIQFileError,
    ReceiverNotOpenError,
)
from .io import IQMetadata, IQReader, IQRecorder
from .simulator import (
    ADCQuantization,
    AntennaPattern,
    ArraySpec,
    Calibration,
    ChannelImpairments,
    ChannelModel,
    CompositeChannel,
    CompositeReceiverImpairments,
    DCOffset,
    EmitterSpec,
    FreeSpaceChannel,
    IdentityReceiverImpairments,
    IQImbalance,
    LogNormalShadowing,
    MultipathFIRChannel,
    ReceiverImpairments,
    SimulationScenario,
    SyntheticReceiver,
    SyntheticReceiverCapabilities,
    TwoRayGroundChannel,
)

__all__ = [
    "ADCQuantization",
    "AntennaPattern",
    "ArraySpec",
    "Calibration",
    "CalibrationFailedError",
    "ChannelImpairments",
    "ChannelModel",
    "CompositeChannel",
    "CompositeReceiverImpairments",
    "DCOffset",
    "EmitterSpec",
    "FreeSpaceChannel",
    "HardwareError",
    "IQImbalance",
    "IQMetadata",
    "IQReader",
    "IQRecorder",
    "IdentityReceiverImpairments",
    "InvalidReadSizeError",
    "LogNormalShadowing",
    "MalformedIQFileError",
    "MultipathFIRChannel",
    "RTLSDRDevice",
    "RTLSDRDeviceCapabilities",
    "ReceiverImpairments",
    "ReceiverNotOpenError",
    "SimulationScenario",
    "SyntheticReceiver",
    "SyntheticReceiverCapabilities",
    "TwoRayGroundChannel",
]
