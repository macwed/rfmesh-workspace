"""rfmesh-sdr.simulator -- the synthetic Receiver and its scenario types.

Re-exports the public surface of the simulator subpackage so callers
write ``from rfmesh_sdr.simulator import SyntheticReceiver`` rather than
fishing through module paths.
"""

from __future__ import annotations

from .antenna import AntennaPattern
from .array import ArraySpec
from .calibration import Calibration
from .capabilities import (
    SyntheticReceiverCapabilities,
    SyntheticTransmitterCapabilities,
)
from .channel import (
    ChannelModel,
    CompositeChannel,
    FreeSpaceChannel,
    LogNormalShadowing,
    MultipathFIRChannel,
    TwoRayGroundChannel,
)
from .emitter import EmitterSpec
from .impairments import (
    ADCQuantization,
    ChannelImpairments,
    CompositeReceiverImpairments,
    DCOffset,
    IdentityReceiverImpairments,
    IQImbalance,
    ReceiverImpairments,
)
from .loopback_channel import LoopbackChannel, LoopbackReceiver
from .noise import complex_awgn, complex_awgn_2d
from .rng import RngManager
from .scenario import SimulationScenario
from .synthetic_receiver import SyntheticReceiver
from .synthetic_transmitter import SyntheticTransmitter

__all__ = [
    "ADCQuantization",
    "AntennaPattern",
    "ArraySpec",
    "Calibration",
    "ChannelImpairments",
    "ChannelModel",
    "CompositeChannel",
    "CompositeReceiverImpairments",
    "DCOffset",
    "EmitterSpec",
    "FreeSpaceChannel",
    "IQImbalance",
    "IdentityReceiverImpairments",
    "LogNormalShadowing",
    "LoopbackChannel",
    "LoopbackReceiver",
    "MultipathFIRChannel",
    "ReceiverImpairments",
    "RngManager",
    "SimulationScenario",
    "SyntheticReceiver",
    "SyntheticReceiverCapabilities",
    "SyntheticTransmitter",
    "SyntheticTransmitterCapabilities",
    "TwoRayGroundChannel",
    "complex_awgn",
    "complex_awgn_2d",
]
