"""Software unit tests for ``RTLSDRDevice`` (WS-A-005).

Hardware is mocked: ``shutil.which`` resolves the binary names, and
``subprocess.Popen`` / ``subprocess.run`` are patched to return scripted
stdout. No real RTL-SDR dongle is touched. The live-hardware path is
covered by ``tests/hardware/test_rtlsdr_live.py`` under the
``hardware`` pytest mark (opt-in).
"""

from __future__ import annotations

import subprocess
from typing import Any

import numpy as np
import pytest
from pydantic import AnyUrl
from rfmesh_contracts.config import BearerConfig, NodeConfig, SDRConfig
from rfmesh_contracts.enums import BearerKind, Capability
from rfmesh_contracts.geospatial import GeodeticPosition
from rfmesh_contracts.protocols import Receiver
from rfmesh_sdr.devices import RTLSDRDevice, RTLSDRDeviceCapabilities
from rfmesh_sdr.exceptions import (
    HardwareError,
    InvalidReadSizeError,
    ReceiverNotOpenError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_RATE_HZ = 2_400_000.0
_CENTER_FREQ_HZ = 915_000_000.0


def _node_config(
    sample_rate_hz: float = _SAMPLE_RATE_HZ,
    center_freq_hz: float = _CENTER_FREQ_HZ,
    gain_db: float | str = 30.0,
) -> NodeConfig:
    """Build a minimal L1-RSSI NodeConfig with driver='rtlsdr'."""
    return NodeConfig(
        node_id="rtlsdr-test-node",
        position=GeodeticPosition(lat_deg=52.0, lon_deg=21.0),
        heading_deg=0.0,
        sdr=SDRConfig(
            driver="rtlsdr",
            sample_rate_hz=sample_rate_hz,
            center_freq_hz=center_freq_hz,
            gain_db=gain_db,  # type: ignore[arg-type]
        ),
        capabilities=(Capability.L1_RSSI,),
        bearer=BearerConfig(kind=BearerKind.WIFI),
        fusion_endpoint=AnyUrl("udp://127.0.0.1:9000"),
    )


class _FakeStdout:
    """Minimal stdout shim that drains bytes from a pre-loaded buffer."""

    def __init__(self, payload: bytes) -> None:
        self._buf = payload
        self._closed = False

    def read(self, n: int) -> bytes:
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk

    def close(self) -> None:
        self._closed = True


class _FakePopen:
    """Drop-in for ``subprocess.Popen`` returning scripted stdout."""

    def __init__(self, payload: bytes, *, returncode_after_drain: int = 0) -> None:
        self.stdout = _FakeStdout(payload)
        self._returncode_after_drain = returncode_after_drain
        self._terminated = False
        self.args: list[str] = []

    def poll(self) -> int | None:
        if self._terminated:
            return self._returncode_after_drain
        return None

    def terminate(self) -> None:
        self._terminated = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._terminated = True
        return self._returncode_after_drain

    def kill(self) -> None:
        self._terminated = True


def _patch_rtl_sdr_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend ``rtl_sdr`` and ``rtl_eeprom`` are on PATH."""
    monkeypatch.setattr(
        "rfmesh_sdr.devices.rtlsdr.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )


def _patch_popen_with_payload(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> dict[str, Any]:
    """Patch ``subprocess.Popen`` so ``rtl_sdr -`` streams ``payload``."""
    captured: dict[str, Any] = {"argv": None}

    def _fake_popen(argv: list[str], **kwargs: Any) -> _FakePopen:
        del kwargs
        captured["argv"] = list(argv)
        return _FakePopen(payload)

    monkeypatch.setattr(
        "rfmesh_sdr.devices.rtlsdr.subprocess.Popen",
        _fake_popen,
    )
    return captured


# ---------------------------------------------------------------------------
# Protocol conformance and capability shape
# ---------------------------------------------------------------------------


def test_implements_receiver_protocol() -> None:
    """``RTLSDRDevice`` structurally satisfies the ``Receiver`` Protocol."""
    device = RTLSDRDevice()
    assert isinstance(device, Receiver)


def test_capabilities_shape() -> None:
    """``capabilities()`` returns the four Protocol-required fields, honest."""
    device = RTLSDRDevice()
    caps = device.capabilities()
    assert isinstance(caps, RTLSDRDeviceCapabilities)
    assert caps.driver == "rtlsdr"
    assert caps.n_coherent_channels == 1
    assert caps.is_power_calibrated is False


def test_actual_sample_rate_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """``configure(rate)`` surfaces that rate on ``capabilities()`` -- no silent shortfall."""
    _patch_rtl_sdr_available(monkeypatch)
    device = RTLSDRDevice()
    device.open()
    device.configure(_node_config(sample_rate_hz=2_400_000.0))
    caps = device.capabilities()
    assert caps.actual_sample_rate_hz == 2_400_000.0
    device.close()


# ---------------------------------------------------------------------------
# read(n) exact-or-raise (B3)
# ---------------------------------------------------------------------------


def test_read_exact_n_returns_complex64(monkeypatch: pytest.MonkeyPatch) -> None:
    """``read(n)`` drains exactly ``n`` complex64 samples on a healthy stream."""
    _patch_rtl_sdr_available(monkeypatch)
    n = 1024
    # Two bytes per sample (interleaved uint8 I/Q). Centre-value payload
    # (127, 128) so the conversion produces near-zero complex samples.
    payload = bytes([127, 128] * n)
    _patch_popen_with_payload(monkeypatch, payload)
    device = RTLSDRDevice()
    device.open()
    device.configure(_node_config())
    samples = device.read(n)
    assert samples.dtype == np.complex64
    assert samples.shape == (n,)
    device.close()


def test_read_short_stdout_raises_hardware_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short stdout read -> ``HardwareError``, never a zero-padded buffer."""
    _patch_rtl_sdr_available(monkeypatch)
    # Ask for 1024 samples (2048 bytes); deliver half.
    payload = bytes([127, 128] * 512)
    _patch_popen_with_payload(monkeypatch, payload)
    device = RTLSDRDevice()
    device.open()
    device.configure(_node_config())
    with pytest.raises(HardwareError, match="rtl_sdr stdout returned"):
        device.read(1024)
    device.close()


def test_read_before_open_raises() -> None:
    """``read()`` before ``open()`` raises ``ReceiverNotOpenError``."""
    device = RTLSDRDevice()
    with pytest.raises(ReceiverNotOpenError):
        device.read(1024)


def test_read_before_configure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """``read()`` after ``open()`` but before ``configure()`` raises."""
    _patch_rtl_sdr_available(monkeypatch)
    device = RTLSDRDevice()
    device.open()
    with pytest.raises(ReceiverNotOpenError, match="before configure"):
        device.read(1024)


def test_read_zero_or_negative_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """``read(n)`` with ``n <= 0`` raises ``InvalidReadSizeError``."""
    _patch_rtl_sdr_available(monkeypatch)
    device = RTLSDRDevice()
    device.open()
    device.configure(_node_config())
    with pytest.raises(InvalidReadSizeError):
        device.read(0)
    with pytest.raises(InvalidReadSizeError):
        device.read(-5)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_lifecycle_open_configure_read_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full lifecycle: open -> configure -> read -> close. No exceptions."""
    _patch_rtl_sdr_available(monkeypatch)
    n = 512
    payload = bytes([127, 128] * n)
    _patch_popen_with_payload(monkeypatch, payload)
    device = RTLSDRDevice()
    device.open()
    device.configure(_node_config())
    out = device.read(n)
    assert out.shape == (n,)
    device.close()


def test_close_idempotent() -> None:
    """``close()`` on a never-opened device is safe; double-close is safe."""
    device = RTLSDRDevice()
    device.close()
    device.close()
    # No exception -> pass.


def test_close_terminates_stream_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """``close()`` reaps the streaming subprocess so a kill-stuck process does not leak."""
    _patch_rtl_sdr_available(monkeypatch)
    n = 256
    payload = bytes([127, 128] * n)
    _patch_popen_with_payload(monkeypatch, payload)
    device = RTLSDRDevice()
    device.open()
    device.configure(_node_config())
    device.read(n)
    # Subprocess was spawned; capture it for the assertion.
    stream_proc = device._stream_process
    assert stream_proc is not None
    device.close()
    assert device._stream_process is None


# ---------------------------------------------------------------------------
# Bounds validation
# ---------------------------------------------------------------------------


def test_configure_rejects_freq_below_tuner_min(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configure with 1 MHz centre frequency -> HardwareError."""
    _patch_rtl_sdr_available(monkeypatch)
    device = RTLSDRDevice()
    device.open()
    with pytest.raises(HardwareError, match="frequency"):
        device.configure(_node_config(center_freq_hz=1_000_000.0))


def test_configure_rejects_sample_rate_above_tuner_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configure with 10 MS/s sample rate -> HardwareError."""
    _patch_rtl_sdr_available(monkeypatch)
    device = RTLSDRDevice()
    device.open()
    with pytest.raises(HardwareError, match="sample rate"):
        device.configure(_node_config(sample_rate_hz=10_000_000.0))


# ---------------------------------------------------------------------------
# Serial resolution
# ---------------------------------------------------------------------------


def test_serial_resolution_prefers_serial_over_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a serial is provided, ``open()`` resolves to that serial's index."""
    _patch_rtl_sdr_available(monkeypatch)

    eeprom_outputs = {
        0: b"Found 1 device(s):\n  0:  Generic RTL2832U\nSerial number: 00000001\n",
        1: b"Found 1 device(s):\n  0:  Generic RTL2832U\nSerial number: 00000077\n",
    }

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        idx = int(argv[2])
        if idx in eeprom_outputs:
            return subprocess.CompletedProcess(
                argv,
                returncode=0,
                stdout=eeprom_outputs[idx],
                stderr=b"",
            )
        return subprocess.CompletedProcess(argv, returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        "rfmesh_sdr.devices.rtlsdr.subprocess.run",
        _fake_run,
    )
    device = RTLSDRDevice(device_index=0, serial="00000077")
    device.open()
    # _device_index is set from the serial resolution, not the constructor.
    assert device._device_index == 1


def test_serial_resolution_tolerates_nonzero_exit_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: rtl_eeprom on some distros (Fedora 44 etc.) returns
    exit code 1 even on successful reads. The probe loop must parse the
    output and accept the serial regardless of exit code."""
    _patch_rtl_sdr_available(monkeypatch)

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        idx = int(argv[2])
        if idx == 0:
            return subprocess.CompletedProcess(
                argv,
                returncode=1,  # <-- nonzero exit even though read succeeded
                stdout=b"Found 2 device(s):\n  0:  Generic\nSerial number: 00000001\n",
                stderr=b"",
            )
        if idx == 1:
            return subprocess.CompletedProcess(
                argv,
                returncode=1,
                stdout=b"Found 2 device(s):\n  1:  Generic\nSerial number: 00000002\n",
                stderr=b"",
            )
        return subprocess.CompletedProcess(argv, returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        "rfmesh_sdr.devices.rtlsdr.subprocess.run",
        _fake_run,
    )
    device = RTLSDRDevice(serial="00000002")
    device.open()
    assert device._device_index == 1


def test_serial_resolution_unknown_serial_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown serial -> HardwareError with the enumerated list."""
    _patch_rtl_sdr_available(monkeypatch)

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        idx = int(argv[2])
        if idx == 0:
            return subprocess.CompletedProcess(
                argv,
                returncode=0,
                stdout=b"Serial number: 00000001\n",
                stderr=b"",
            )
        return subprocess.CompletedProcess(argv, returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        "rfmesh_sdr.devices.rtlsdr.subprocess.run",
        _fake_run,
    )
    device = RTLSDRDevice(serial="DEADBEEF")
    with pytest.raises(HardwareError, match="DEADBEEF"):
        device.open()


# ---------------------------------------------------------------------------
# rtl_sdr missing-binary path
# ---------------------------------------------------------------------------


def test_open_raises_when_rtl_sdr_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """``open()`` without ``rtl_sdr`` on PATH raises ``HardwareError``."""
    monkeypatch.setattr(
        "rfmesh_sdr.devices.rtlsdr.shutil.which",
        lambda _name: None,
    )
    device = RTLSDRDevice()
    with pytest.raises(HardwareError, match="rtl_sdr binary not found"):
        device.open()
