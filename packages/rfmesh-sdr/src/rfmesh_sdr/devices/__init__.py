"""rfmesh-sdr.devices -- hardware-backed Receiver implementations.

Each module here exposes one concrete class that structurally satisfies
``rfmesh_contracts.protocols.Receiver`` (or, for coherent dongles,
``CoherentReceiver``). The Protocol structural-typing path means no
ABC inheritance -- the synthetic and hardware receivers are
interchangeable from the node runtime's perspective.

WS-A-005 ships ``RTLSDRDevice`` (single-channel RTL-SDR V4 via the
``rtl_sdr`` subprocess). Coherent dongles (bladeRF / Pluto+) and the
generic Soapy path land in later tickets.
"""

from __future__ import annotations

from .rtlsdr import RTLSDRDevice, RTLSDRDeviceCapabilities

__all__ = [
    "RTLSDRDevice",
    "RTLSDRDeviceCapabilities",
]
