"""Receiver-capabilities surface for the synthetic simulator.

A frozen dataclass that structurally satisfies
``rfmesh_contracts.protocols.ReceiverCapabilities``. The four attributes
the Protocol requires (``driver``, ``n_coherent_channels``,
``actual_sample_rate_hz``, ``is_power_calibrated``) are exposed as
ordinary fields rather than ``@property`` because the dataclass is read
once per ``SyntheticReceiver.capabilities()`` call and immediately
discarded -- no benefit from late binding.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyntheticReceiverCapabilities:
    """Capability snapshot for ``SyntheticReceiver``.

    Honest defaults: ``is_power_calibrated`` is False (no SDR in scope is
    power-calibrated; the simulator inherits the same honesty -- see
    ``INHERITED_CONTEXT.md`` Section 1.3). ``n_coherent_channels`` is 1
    because WS-A-001 ships the single-channel mode only; coherent mode is
    WS-A-002.
    """

    driver: str
    n_coherent_channels: int
    actual_sample_rate_hz: float
    is_power_calibrated: bool
