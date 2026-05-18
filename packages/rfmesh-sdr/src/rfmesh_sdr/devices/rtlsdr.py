"""RTL-SDR V4 ``Receiver`` implementation backed by the ``rtl_sdr`` subprocess.

Port of ``macwed/rf-mesh:rfmesh/io/devices/rtlsdr.py`` (~415 LoC, proven
prior art) to the new ``rfmesh_contracts.protocols.Receiver`` Protocol
seam. The salvage's ``Device`` ABC -> Protocol structural-typing swap and
the ``CaptureRequest`` / ``CaptureResult`` -> direct ``read(n)`` re-cut
are the only architectural deltas; the subprocess pattern, the chunked
stdout-read, the capability bounds, and the ``rtl_eeprom`` serial-
resolution path all come across intact.

Lifecycle:

  open()          -- find ``rtl_sdr`` on PATH; resolve serial to index.
  configure(cfg)  -- store SDRConfig tuning; bounds-check against V4 limits.
  read(n)         -- spawn the ``rtl_sdr -`` stream on first call after
                     configure, then drain exactly ``n`` complex samples
                     from its stdout (or raise -- no silent short reads).
  capabilities()  -- snapshot dataclass (driver='rtlsdr',
                     n_coherent_channels=1, actual_sample_rate_hz from
                     configure, is_power_calibrated=False).
  close()         -- terminate the subprocess; idempotent.

The ``read(n)`` Invariant-B3 guarantee (exactly n samples or raise) is
enforced by reading ``n * 2`` bytes from stdout and treating any short
read as a hardware-side failure -- the salvaged ``stream()`` generator's
"short read -> break out" behaviour is **not** carried over; that is
the silent-fallback the Protocol contract explicitly bans.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from rfmesh_sdr.exceptions import (
    HardwareError,
    InvalidReadSizeError,
    ReceiverNotOpenError,
)
from rfmesh_sdr.io.constants import BYTES_PER_SAMPLE
from rfmesh_sdr.io.conversion import uint8_pair_to_complex64

if TYPE_CHECKING:
    from rfmesh_contracts.config import NodeConfig

# RTL-SDR V4 datasheet limits.
_FREQ_MIN_HZ: int = 24_000_000
_FREQ_MAX_HZ: int = 1_766_000_000
_SAMPLE_RATE_MIN_HZ: int = 225_001
_SAMPLE_RATE_MAX_HZ: int = 3_200_000

# Serial resolution probes at most this many device indices.
_MAX_DEVICE_PROBE: int = 8

# Subprocess teardown grace before SIGKILL.
_TERMINATE_TIMEOUT_S: float = 2.0


@dataclass(frozen=True)
class RTLSDRDeviceCapabilities:
    """Capability snapshot for ``RTLSDRDevice``.

    Mirrors the four attributes of ``rfmesh_contracts.protocols.
    ReceiverCapabilities`` as plain fields rather than properties --
    consumed once per ``capabilities()`` call and discarded. The values
    are populated from the configured tuning, so ``capabilities()`` is
    only meaningful after ``configure()``.
    """

    driver: str
    n_coherent_channels: int
    actual_sample_rate_hz: float
    is_power_calibrated: bool


class RTLSDRDevice:
    """RTL-SDR V4 dongle, addressed by index or by serial.

    Single-channel only -- the V4 chip does not expose phase-coherent
    multi-RX so this class structurally satisfies ``Receiver`` but not
    ``CoherentReceiver``. Coherent receivers (bladeRF, Pluto+) ship in a
    separate ticket.

    The streaming subprocess is spawned lazily on the first ``read()``
    after a successful ``configure()`` so the unit tests can patch
    ``subprocess.Popen`` and ``shutil.which`` without needing a real
    binary on PATH or a real device attached.
    """

    def __init__(self, device_index: int = 0, serial: str | None = None) -> None:
        """Configure a device handle.

        Args:
            device_index: ``rtl_sdr -d`` index. Ignored if ``serial`` is set
                at ``open()`` time.
            serial: Optional serial string. When given, ``open()`` resolves
                the index via ``rtl_eeprom`` enumeration.
        """
        self._device_index = device_index
        self._serial = serial
        self._is_open: bool = False
        self._sdr_driver: str | None = None
        self._actual_sample_rate_hz: float = 0.0
        self._configured_freq_hz: int = 0
        self._configured_gain_db: float | str = "auto"
        self._stream_process: subprocess.Popen[bytes] | None = None

    # ------------------------------------------------------------------
    # Receiver Protocol surface
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Acquire the device: locate ``rtl_sdr``, resolve serial if given.

        Idempotent-friendly: calling ``open()`` twice does not raise; the
        second call re-resolves the serial in case the bus enumeration
        changed (a dongle hot-plugged between calls).
        """
        if shutil.which("rtl_sdr") is None:
            msg = (
                "rtl_sdr binary not found in PATH -- "
                "install rtl-sdr (e.g. 'sudo apt install rtl-sdr')."
            )
            raise HardwareError(msg)
        if self._serial is not None:
            self._device_index = self._resolve_serial(self._serial)
        self._is_open = True

    def configure(self, config: NodeConfig) -> None:
        """Apply tuning from ``config.sdr``. Validates against V4 bounds.

        Raises:
            HardwareError: if the configured frequency or sample rate is
                outside the V4 tuner range. Surfaced explicitly so a
                config typo is caught at startup, not as a confusing
                downstream IQ artefact.
        """
        sdr = config.sdr
        freq_hz = int(sdr.center_freq_hz)
        rate_hz = int(sdr.sample_rate_hz)
        if not (_FREQ_MIN_HZ <= freq_hz <= _FREQ_MAX_HZ):
            msg = (
                f"frequency {freq_hz / 1e6:g} MHz outside RTL-SDR V4 range "
                f"[{_FREQ_MIN_HZ / 1e6:g}, {_FREQ_MAX_HZ / 1e6:g}] MHz"
            )
            raise HardwareError(msg)
        if not (_SAMPLE_RATE_MIN_HZ <= rate_hz <= _SAMPLE_RATE_MAX_HZ):
            msg = (
                f"sample rate {rate_hz} Hz outside RTL-SDR V4 range "
                f"[{_SAMPLE_RATE_MIN_HZ}, {_SAMPLE_RATE_MAX_HZ}] Hz"
            )
            raise HardwareError(msg)
        self._sdr_driver = sdr.driver
        self._actual_sample_rate_hz = float(rate_hz)
        self._configured_freq_hz = freq_hz
        self._configured_gain_db = sdr.gain_db
        # If a stream was already running from a prior configure cycle,
        # tear it down so the next read() spawns a fresh subprocess with
        # the new tuning. (configure-after-read is a legitimate flow --
        # e.g. an L1 sweep that retunes between dwells.)
        self._cleanup_stream()

    def read(self, n_samples: int) -> np.ndarray:
        """Return exactly ``n_samples`` complex64 IQ samples or raise.

        Spawns the ``rtl_sdr -`` streaming subprocess lazily on the first
        call after a configure(); subsequent calls drain from the same
        subprocess. A short read from stdout (process died, USB unplug,
        kernel buffer underrun) is a ``HardwareError`` -- the Receiver
        Protocol contract (Invariant B3) forbids silent zero-padding.

        Raises:
            ReceiverNotOpenError: if ``open()`` has not been called, or
                if ``close()`` has been called since the last ``open()``.
            InvalidReadSizeError: if ``n_samples <= 0``.
            HardwareError: if ``rtl_sdr`` cannot be started, or its stdout
                returned fewer bytes than requested.
        """
        if not self._is_open:
            msg = "RTLSDRDevice.read() called before open() (or after close())."
            raise ReceiverNotOpenError(msg)
        if n_samples <= 0:
            msg = f"RTLSDRDevice.read(n) requires n > 0 (got {n_samples})."
            raise InvalidReadSizeError(msg)
        if self._sdr_driver is None:
            msg = "RTLSDRDevice.read() called before configure()."
            raise ReceiverNotOpenError(msg)
        if self._stream_process is None:
            self._spawn_stream()
        process = self._stream_process
        assert process is not None
        assert process.stdout is not None
        need_bytes = n_samples * BYTES_PER_SAMPLE
        raw = process.stdout.read(need_bytes)
        if len(raw) != need_bytes:
            # Short read -> hardware-side failure. Drain stderr for the
            # operator's diagnostic and tear down.
            rc = process.poll()
            self._cleanup_stream()
            msg = (
                f"rtl_sdr stdout returned {len(raw)} bytes for {need_bytes} "
                f"requested (n_samples={n_samples}, returncode={rc}, "
                f"cfg: {self._configured_freq_hz / 1e6:g} MHz @ "
                f"{self._actual_sample_rate_hz / 1e6:g} MS/s). "
                "USB unplug, kernel buffer underrun, or rtl_sdr crash."
            )
            raise HardwareError(msg)
        samples = np.frombuffer(raw, dtype=np.uint8)
        return uint8_pair_to_complex64(samples)

    def capabilities(self) -> RTLSDRDeviceCapabilities:
        """Return the capability snapshot. See ``RTLSDRDeviceCapabilities``.

        ``is_power_calibrated`` is False -- the RTL-SDR V4 ADC is not
        calibrated against absolute power (``INHERITED_CONTEXT.md``
        Section 1.3). ``n_coherent_channels`` is 1 -- the V4 chip
        exposes a single tuner; phase-coherent multi-RX needs a
        different dongle family (bladeRF, Pluto+).

        ``actual_sample_rate_hz`` carries the rate from the last
        ``configure()`` call -- the salvage path used ``rtl_sdr -s
        <rate>``, which the kernel quantises to the nearest achievable
        rate (e.g. 2.4 MS/s requested may come back at 2 400 000 or
        2 399 962 Hz). The V4 datasheet covers the achievable set; this
        method surfaces the configured value, and a more granular probe
        is a future ticket (B3 surface for sample-rate honesty).
        """
        return RTLSDRDeviceCapabilities(
            driver="rtlsdr",
            n_coherent_channels=1,
            actual_sample_rate_hz=self._actual_sample_rate_hz,
            is_power_calibrated=False,
        )

    def close(self) -> None:
        """Release the device. Idempotent; safe in teardown paths."""
        self._cleanup_stream()
        self._is_open = False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _spawn_stream(self) -> None:
        """Start the long-running ``rtl_sdr -`` subprocess."""
        gain = self._configured_gain_db
        gain_arg = "auto" if gain == "auto" else f"{float(gain):g}"
        cmd = [
            "rtl_sdr",
            "-d",
            str(self._device_index),
            "-f",
            str(self._configured_freq_hz),
            "-s",
            str(int(self._actual_sample_rate_hz)),
            "-g",
            gain_arg,
            "-",
        ]
        try:
            self._stream_process = subprocess.Popen(  # noqa: S603 -- argv list, no shell
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            msg = f"failed to start rtl_sdr subprocess: {exc}"
            raise HardwareError(msg) from exc

    def _resolve_serial(self, target_serial: str) -> int:
        """Resolve a serial string to a ``rtl_sdr -d`` index via ``rtl_eeprom``.

        Probes indices 0..``_MAX_DEVICE_PROBE - 1`` until an attached
        dongle reports the matching ``Serial number:`` field. Raises
        ``HardwareError`` with the enumeration list if no match.
        """
        found_serials: list[str] = []
        for idx in range(_MAX_DEVICE_PROBE):
            result = subprocess.run(  # noqa: S603 -- argv list, no shell
                ["rtl_eeprom", "-d", str(idx)],  # noqa: S607 -- intentional PATH lookup
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                break
            stdout = result.stdout.decode(errors="replace")
            stderr = result.stderr.decode(errors="replace")
            serial = _parse_serial(stdout) or _parse_serial(stderr)
            if serial is None:
                continue
            found_serials.append(serial)
            if serial == target_serial:
                return idx
        msg = f"no RTL-SDR with serial {target_serial!r} found, available: {found_serials}"
        raise HardwareError(msg)

    def _cleanup_stream(self) -> None:
        """Terminate the streaming subprocess. Idempotent."""
        process = self._stream_process
        if process is None:
            return
        self._stream_process = None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_TERMINATE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout is not None:
            process.stdout.close()


def _parse_serial(text: str) -> str | None:
    """Extract a ``Serial number:`` line from ``rtl_eeprom`` output."""
    for line in text.splitlines():
        if "Serial number" in line and ":" in line:
            return line.split(":", 1)[1].strip()
    return None
