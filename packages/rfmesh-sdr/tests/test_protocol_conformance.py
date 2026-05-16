"""Acceptance criteria 1(a), 1(b), and 1(i): Protocol conformance for the simulator types.

WS-A-001 covered ``SyntheticReceiver`` against ``rfmesh_contracts.Receiver``.
WS-A-003 adds runtime-checkable Protocol surfaces for the channel-model and
receiver-impairment plug-ins so the simulator's wire surfaces are
structurally typed end-to-end.
"""

from __future__ import annotations

import numpy as np
from rfmesh_contracts import Receiver  # type: ignore[import-untyped, unused-ignore]
from rfmesh_sdr import (
    ADCQuantization,
    ChannelModel,
    CompositeChannel,
    CompositeReceiverImpairments,
    DCOffset,
    FreeSpaceChannel,
    IdentityReceiverImpairments,
    IQImbalance,
    LogNormalShadowing,
    MultipathFIRChannel,
    ReceiverImpairments,
    SimulationScenario,
    SyntheticReceiver,
    TwoRayGroundChannel,
)


def test_synthetic_receiver_is_receiver(default_scenario: SimulationScenario) -> None:
    """The Receiver Protocol is @runtime_checkable; SyntheticReceiver is one."""
    receiver = SyntheticReceiver(default_scenario)
    assert isinstance(receiver, Receiver)


def test_capabilities_shape(default_scenario: SimulationScenario) -> None:
    """Capabilities advertise the simulator's honest defaults."""
    receiver = SyntheticReceiver(default_scenario)
    caps = receiver.capabilities()
    assert caps.driver == "sim"
    assert caps.n_coherent_channels == 1
    assert caps.is_power_calibrated is False
    assert caps.actual_sample_rate_hz == default_scenario.sample_rate_hz


def test_channel_model_protocol_conformance() -> None:
    """All four channel implementations + CompositeChannel satisfy the Protocol.

    ``ChannelModel`` is ``@runtime_checkable``; structural typing means the
    isinstance check verifies the implementation's ``apply`` signature shape
    matches without requiring inheritance.
    """
    free_space = FreeSpaceChannel()
    two_ray = TwoRayGroundChannel(height_tx_m=2.0, height_rx_m=1.5)
    multipath = MultipathFIRChannel(
        taps=np.array([1.0 + 0.0j], dtype=np.complex128),
        delays=np.array([0], dtype=np.int64),
    )
    log_normal = LogNormalShadowing(sigma_db=4.0)
    composite = CompositeChannel(channels=(free_space, two_ray, multipath, log_normal))

    for channel in (free_space, two_ray, multipath, log_normal, composite):
        assert isinstance(channel, ChannelModel), (
            f"{type(channel).__name__} does not structurally satisfy ChannelModel."
        )


def test_receiver_impairments_protocol_conformance() -> None:
    """All five receiver-impairment implementations satisfy the Protocol.

    ``ReceiverImpairments`` is ``@runtime_checkable`` for the same reason
    ``ChannelModel`` is: ``SyntheticReceiver`` calls ``.apply(samples, rng)``
    on whatever the scenario passes, and an instance that does not match
    the shape is a configuration error caught here, not at render time.
    """
    identity = IdentityReceiverImpairments()
    iq_imb = IQImbalance(amplitude_db=0.5, phase_deg=3.0)
    dc = DCOffset(i_volts=0.01, q_volts=-0.01)
    adc = ADCQuantization(bits=8)
    composite = CompositeReceiverImpairments(impairments=(dc, iq_imb, adc))

    for impair in (identity, iq_imb, dc, adc, composite):
        assert isinstance(impair, ReceiverImpairments), (
            f"{type(impair).__name__} does not structurally satisfy ReceiverImpairments."
        )
