"""Tests for ``rfmesh_node.capabilities``.

The startup gate that intersects declared ``NodeConfig.capabilities``
with the live receiver's ``ReceiverCapabilities``. ~8 tests covering:

* L1 declared + 1-channel receiver -> active L1.
* L2 declared + 1-channel receiver -> CapabilityMismatchError.
* L2 declared + coherent receiver (calibrated) -> active L2.
* L2 declared + coherent receiver (NOT calibrated) -> raise.
* ``build_estimators`` for each estimator capability + L2_MVDR_NULL (empty).
"""

from __future__ import annotations

import pytest
from rfmesh_contracts import (
    ArrayConfig,
    ArrayGeometry,
    Capability,
    GeodeticPosition,
    SDRConfig,
)
from rfmesh_node import CapabilityMismatchError, build_estimators, detect_active_capabilities

# ---------------------------------------------------------------------------
# Minimal Receiver Protocol fakes -- enough surface for capabilities() and
# the L2 isinstance(receiver, CoherentReceiver) check.
# ---------------------------------------------------------------------------


class _Caps:
    def __init__(self, *, driver: str, n_channels: int) -> None:
        self.driver = driver
        self.n_coherent_channels = n_channels
        self.actual_sample_rate_hz = 2_400_000.0
        self.is_power_calibrated = False


class _SingleChannelReceiver:
    """Looks like a Receiver, not a CoherentReceiver -- L1 only."""

    def __init__(self) -> None:
        self._caps = _Caps(driver="rtlsdr", n_channels=1)

    def open(self) -> None: ...
    def configure(self, config: object) -> None: ...
    def read(self, n_samples: int) -> object:
        raise NotImplementedError

    def capabilities(self) -> _Caps:
        return self._caps

    def close(self) -> None: ...


class _CoherentReceiver:
    """Structurally a CoherentReceiver -- has read_coherent / calibrate."""

    def __init__(self, *, n_channels: int = 2, calibrated: bool = True) -> None:
        self._caps = _Caps(driver="bladerf", n_channels=n_channels)
        self._calibrated = calibrated

    def open(self) -> None: ...
    def configure(self, config: object) -> None: ...
    def read(self, n_samples: int) -> object:
        raise NotImplementedError

    def read_coherent(self, n_samples: int) -> object:
        raise NotImplementedError

    def calibrate(self) -> None:
        self._calibrated = True

    @property
    def is_calibrated(self) -> bool:
        return self._calibrated

    def capabilities(self) -> _Caps:
        return self._caps

    def close(self) -> None: ...


def _node_pos() -> GeodeticPosition:
    return GeodeticPosition(lat_deg=50.85, lon_deg=4.35, hae_m=50.0, sigma_m=5.0)


def _sdr(driver: str = "sim") -> SDRConfig:
    return SDRConfig(
        driver=driver,  # type: ignore[arg-type]
        sample_rate_hz=2_400_000.0,
        center_freq_hz=915_000_000.0,
        gain_db=30.0,
    )


def _l2_array() -> ArrayConfig:
    return ArrayConfig(
        geometry=ArrayGeometry.ULA,
        n_elements=2,
        element_spacing_m=0.164,
    )


# ---------------------------------------------------------------------------
# detect_active_capabilities
# ---------------------------------------------------------------------------


def test_l1_declared_single_channel_returns_l1() -> None:
    """L1 declared + a non-coherent Receiver returns (L1_RSSI,)."""
    rx = _SingleChannelReceiver()
    active = detect_active_capabilities(
        declared=(Capability.L1_RSSI,),
        receiver=rx,
        array=None,
    )
    assert active == (Capability.L1_RSSI,)


def test_l2_declared_single_channel_raises() -> None:
    """L2_MUSIC declared but the receiver has only 1 coherent channel -> raise."""
    rx = _SingleChannelReceiver()
    with pytest.raises(CapabilityMismatchError) as excinfo:
        detect_active_capabilities(
            declared=(Capability.L2_MUSIC,),
            receiver=rx,
            array=_l2_array(),
        )
    assert "l2_music" in str(excinfo.value).lower()
    assert "coherentreceiver" in str(excinfo.value).lower()


def test_l2_declared_coherent_calibrated_returns_l2() -> None:
    """L2_MUSIC + calibrated CoherentReceiver -> active set carries L2."""
    rx = _CoherentReceiver(n_channels=2, calibrated=True)
    active = detect_active_capabilities(
        declared=(Capability.L2_MUSIC,),
        receiver=rx,
        array=_l2_array(),
    )
    assert active == (Capability.L2_MUSIC,)


def test_l2_declared_coherent_uncalibrated_raises() -> None:
    """L2 declared + CoherentReceiver where is_calibrated=False -> raise (B3).

    INTERFACES.md §5 ``CoherentReceiver``: the L2 DSP code MUST refuse to
    emit on an uncalibrated coherent stream. We refuse to boot the node
    rather than silently downgrade. Documented behaviour: raise.
    """
    rx = _CoherentReceiver(n_channels=2, calibrated=False)
    with pytest.raises(CapabilityMismatchError) as excinfo:
        detect_active_capabilities(
            declared=(Capability.L2_MUSIC,),
            receiver=rx,
            array=_l2_array(),
        )
    msg = str(excinfo.value).lower()
    assert "calibrat" in msg


def test_l2_declared_no_array_raises() -> None:
    """L2 declared but NodeConfig.array is None -> raise."""
    rx = _CoherentReceiver(n_channels=2, calibrated=True)
    with pytest.raises(CapabilityMismatchError) as excinfo:
        detect_active_capabilities(
            declared=(Capability.L2_MUSIC,),
            receiver=rx,
            array=None,
        )
    assert "arrayconfig" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# build_estimators -- dispatch table behaviour.
# ---------------------------------------------------------------------------


def test_build_estimators_l1_returns_l1_estimator() -> None:
    rx = _SingleChannelReceiver()
    estimators = build_estimators(
        active=(Capability.L1_RSSI,),
        sdr_config=_sdr(),
        array_config=None,
        heading_deg=0.0,
        node_id="node-a",
        node_position=_node_pos(),
        receiver=rx,
    )
    assert len(estimators) == 1
    assert estimators[0].method is Capability.L1_RSSI


def test_build_estimators_l2_music_returns_music_estimator() -> None:
    rx = _CoherentReceiver(n_channels=2, calibrated=True)
    estimators = build_estimators(
        active=(Capability.L2_MUSIC,),
        sdr_config=_sdr("bladerf"),
        array_config=_l2_array(),
        heading_deg=0.0,
        node_id="node-l2",
        node_position=_node_pos(),
        receiver=rx,
    )
    assert len(estimators) == 1
    assert estimators[0].method is Capability.L2_MUSIC


def test_build_estimators_l2_capon_returns_capon_estimator() -> None:
    rx = _CoherentReceiver(n_channels=2, calibrated=True)
    estimators = build_estimators(
        active=(Capability.L2_CAPON,),
        sdr_config=_sdr("bladerf"),
        array_config=_l2_array(),
        heading_deg=0.0,
        node_id="node-l2",
        node_position=_node_pos(),
        receiver=rx,
    )
    assert len(estimators) == 1
    assert estimators[0].method is Capability.L2_CAPON


def test_build_estimators_l2_mvdr_null_is_not_an_estimator() -> None:
    """L2_MVDR_NULL is utility code, not a BearingEstimator. Empty tuple."""
    rx = _CoherentReceiver(n_channels=2, calibrated=True)
    estimators = build_estimators(
        active=(Capability.L2_MVDR_NULL,),
        sdr_config=_sdr("bladerf"),
        array_config=_l2_array(),
        heading_deg=0.0,
        node_id="node-l2",
        node_position=_node_pos(),
        receiver=rx,
    )
    assert estimators == ()


def test_build_estimators_l3_classify_is_not_an_estimator() -> None:
    """L3_CLASSIFY is a classifier task, not a BearingEstimator. Empty tuple."""
    rx = _SingleChannelReceiver()
    estimators = build_estimators(
        active=(Capability.L3_CLASSIFY,),
        sdr_config=_sdr(),
        array_config=None,
        heading_deg=0.0,
        node_id="node-a",
        node_position=_node_pos(),
        receiver=rx,
    )
    assert estimators == ()
