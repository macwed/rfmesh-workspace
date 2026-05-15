"""``SyntheticReceiver`` -- the simulator implementing Receiver / CoherentReceiver.

WS-A-001 shipped the single-channel L1 path: one antenna, one IQ stream,
the ``Receiver`` Protocol. WS-A-002 extends the class with a coherent
multi-channel path that structurally satisfies ``CoherentReceiver`` when
the scenario carries an ``ArraySpec``.

Two-mode construction
---------------------
``SyntheticReceiver(scenario)`` returns either:

* a plain ``SyntheticReceiver`` instance when ``scenario.array is None`` --
  the WS-A-001 behaviour. ``isinstance(..., Receiver)`` is True;
  ``isinstance(..., CoherentReceiver)`` is False because the coherent
  methods are absent from the class.
* a ``_CoherentSyntheticReceiver`` instance (returned via ``__new__``)
  when ``scenario.array is not None``. The subclass adds
  ``read_coherent``, ``calibrate``, and ``is_calibrated`` to satisfy
  ``CoherentReceiver`` structurally, and overrides ``read`` to return
  row 0 of an internally-rendered coherent block (so ``read(n)`` and
  ``read_coherent(n)[0, :]`` agree byte-for-byte at matched RNG state --
  WS-A-002 Acceptance 1(d)).

The base class still exposes the WS-A-001 surface unchanged. The render
path lives in ``_render_block_single`` (1-D single-channel) and
``_render_block_coherent`` (2-D coherent). Both consume the same scenario
and the same RNG state in the same way as the prior ticket, so reseed
semantics and determinism tests transfer.

Calibration model
-----------------
``calibrate()`` simulates a built-in noise-source handshake (WS-A-002
Decision 2): wideband AWGN is broadcast equally to every channel through
the array's *internal* RF distribution, the per-channel impairments are
applied (impairments live between the noise source and the ADC),
per-channel ADC noise is added, and the receiver measures
``R_i = <x_i * conj(x_0)>``. Signal power is recovered from
``|R_0| - adc_noise_power`` (channel-0 auto-power minus the known
ADC noise floor) -- this is the *unbiased* estimator. The alternative
``mean(|R_i|)`` over ``i > 0`` is biased by ``mean(|offset_i|)``,
leaving a small uniform amplitude residual after correction. Once
``signal_power_est`` is in hand, per-channel offsets are recovered as
``measured_offset_i = R_i / signal_power_est`` and inverted to form the
correction. If the measured SNR falls below the calibration threshold,
``CalibrationFailedError`` is raised and ``is_calibrated`` stays False.
On success, the inverses are stored as a ``Calibration`` record and
applied in subsequent ``read_coherent`` calls so the measured signal
is phase- and gain-aligned to channel 0.

A retune (``configure()``) flips ``is_calibrated`` back to False without
discarding the stored offsets: the contract is "a calibration is valid
only at the tuning at which it was measured", and an operator can call
``calibrate()`` again after the retune.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from rfmesh_contracts import NodeConfig  # type: ignore[import-untyped, unused-ignore]

from ..exceptions import (
    CalibrationFailedError,
    InvalidReadSizeError,
    ReceiverNotOpenError,
)
from .calibration import Calibration
from .capabilities import SyntheticReceiverCapabilities
from .noise import complex_awgn, complex_awgn_2d
from .rng import RngManager
from .scenario import SimulationScenario

# Decibel divisors -- amplitude side (20*log10) for tx_power and dBFS
# conversions, power side (10*log10) for antenna-pattern dB <-> linear.
_DB_AMPLITUDE_DIVISOR = 20.0
_DEG_180 = 180.0
_DEG_360 = 360.0
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_TWO = 2

# Calibration-handshake parameters. The simulator renders this many samples
# of the noise reference; cross-channel statistics converge well below the
# +/-1 degree phase tolerance demanded by Acceptance 1(j) at this length.
_CALIBRATION_N_SAMPLES = 32768
# Minimum measured SNR (dB) required for calibration to succeed. Below this
# threshold the cross-channel correlations are too noisy to recover stable
# offsets and ``calibrate()`` refuses -- matching Invariant 4 ("no silent
# fallbacks") applied to the calibration surface.
_MIN_CALIBRATION_SNR_DB = 10.0
# Floor used to guard log/divide-by-zero in SNR estimation when the
# measured power approaches the IEEE-754 underflow region.
_SIGNAL_POWER_FLOOR = 1e-30


def _wrap_pm180(deg: float) -> float:
    """Wrap an angle in degrees to [-180, 180)."""
    return ((deg + _DEG_180) % _DEG_360) - _DEG_180


class SyntheticReceiver:
    """Synthetic implementation of ``Receiver`` (and ``CoherentReceiver`` when armed).

    Construction parameters fix the scenario and the seed; every other
    state transition goes through the Protocol methods (``open``,
    ``configure``, ``read``, ``capabilities``, ``close``) plus the
    simulator-specific affordances ``set_antenna_heading`` and
    ``reseed`` that the L1 estimator and the test harness consume.

    When the scenario carries an ``ArraySpec``, ``__new__`` returns a
    private ``_CoherentSyntheticReceiver`` subclass instead -- adding
    ``read_coherent``, ``calibrate``, and ``is_calibrated`` to satisfy
    ``CoherentReceiver`` structurally. From the caller's perspective
    they construct ``SyntheticReceiver(scenario)`` and use isinstance
    to discover the capability.
    """

    def __new__(
        cls,
        scenario: SimulationScenario,
        seed: int = 0,
    ) -> SyntheticReceiver:
        """Return a coherent subclass when the scenario carries an array, else self.

        ``isinstance(..., CoherentReceiver)`` is True iff the class defines
        the coherent methods. The factory keeps the methods absent on
        plain-``SyntheticReceiver`` instances so single-channel scenarios
        report a single-channel capability via the Protocol check.
        """
        del seed  # consumed by __init__, ignored here
        if cls is SyntheticReceiver and scenario.array is not None:
            return object.__new__(_CoherentSyntheticReceiver)
        return object.__new__(cls)

    def __init__(self, scenario: SimulationScenario, seed: int = 0) -> None:
        """Bind a scenario and seed; the receiver is constructed *closed*.

        A caller must ``open()`` before ``read()`` -- enforced by
        ``ReceiverNotOpenError`` -- so a freshly constructed receiver
        behaves the same as one that has been ``close()``d, which keeps
        the lifecycle small.
        """
        self._scenario = scenario
        self._rng = RngManager(seed=seed)
        self._heading_deg: float = 0.0
        self._sample_offset: int = 0
        self._is_open: bool = False
        self._node_config: NodeConfig | None = None

    # ------------------------------------------------------------------
    # Receiver Protocol surface
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Mark the receiver opened and zero the sample-offset counter.

        Resetting the counter on every ``open()`` is what makes
        ``open -> read(n) -> close -> open -> read(n)`` deterministic
        without an explicit ``reseed``: same call sequence, same IQ.
        Idempotent: calling ``open()`` twice is harmless (sample-offset
        is just reset again -- the receiver was already open).
        """
        self._is_open = True
        self._sample_offset = 0

    def configure(self, config: NodeConfig) -> None:
        """Record the node config.

        The simulator's sample rate is authoritative from the scenario;
        the ``configure`` call does *not* retune the simulator off the
        passed ``NodeConfig.sdr.sample_rate_hz`` -- conflicting rates are
        a scenario-author error caught by the test fixture, not at
        runtime. Storing the config lets future tickets surface the
        intended tuning to coherent-mode internals.

        On coherent receivers the subclass overrides this to flip
        ``is_calibrated`` to False (a retune invalidates the
        inter-channel phase relationship the prior calibration
        established).
        """
        self._node_config = config

    def read(self, n_samples: int) -> np.ndarray:
        """Return exactly ``n_samples`` of complex64 single-channel IQ.

        Raises:
            ReceiverNotOpenError: if ``open()`` has not been called, or
                if ``close()`` has been called since the last ``open()``.
            InvalidReadSizeError: if ``n_samples <= 0``.

        The Receiver Protocol's "no silent short reads" guarantee (the
        Invariant-4 surface for the L1 path) is met here: either exactly
        n samples come back, or an exception is raised.
        """
        if not self._is_open:
            msg = "SyntheticReceiver.read() called before open() (or after close())."
            raise ReceiverNotOpenError(msg)
        if n_samples <= 0:
            msg = f"SyntheticReceiver.read(n) requires n > 0 (got {n_samples})."
            raise InvalidReadSizeError(msg)
        samples = self._render_block_single(n_samples)
        self._sample_offset += n_samples
        return samples

    def capabilities(self) -> SyntheticReceiverCapabilities:
        """Return the capability snapshot. See ``SyntheticReceiverCapabilities``.

        ``is_power_calibrated`` is False -- the simulator inherits the
        honesty constraint from real SDRs (``INHERITED_CONTEXT.md`` Section
        1.3). ``n_coherent_channels`` reflects the array element count
        when the scenario carries an ``ArraySpec``; otherwise it is 1.
        """
        n_chan = 1 if self._scenario.array is None else self._scenario.array.n_elements
        return SyntheticReceiverCapabilities(
            driver="sim",
            n_coherent_channels=n_chan,
            actual_sample_rate_hz=self._scenario.sample_rate_hz,
            is_power_calibrated=False,
        )

    def close(self) -> None:
        """Mark the receiver closed. Idempotent (callable many times safely)."""
        self._is_open = False

    # ------------------------------------------------------------------
    # Simulator-specific affordances (not on the Receiver Protocol)
    # ------------------------------------------------------------------

    def set_antenna_heading(self, heading_deg: float) -> None:
        """Point the (mock) directional antenna at ``heading_deg`` degrees true.

        Domain is [0, 360) -- matches the contracts' bearing convention
        (INTERFACES.md Section 0). Out-of-range values are an L1-estimator
        bug, not a wrap-around-and-hope case.
        """
        if not (0.0 <= heading_deg < _DEG_360):
            msg = (
                "SyntheticReceiver.set_antenna_heading: heading must be "
                f"in [0, 360) (got {heading_deg})."
            )
            raise ValueError(msg)
        self._heading_deg = heading_deg

    def reseed(self, seed: int) -> None:
        """Reseed every stochastic stream; deterministic geometry is preserved.

        Heading, scenario, sample-offset counter, and per-emitter phase
        are *not* touched -- only the noise (and, in later tickets, the
        per-emitter jitter / shadowing) realisations change. Test
        ``test_reseed_changes_noise_keeps_signal`` is the contract this
        method honours.
        """
        self._rng.reseed(seed)

    @property
    def heading_deg(self) -> float:
        """Current antenna heading, degrees true, [0, 360). Read-only outside the class."""
        return self._heading_deg

    # ------------------------------------------------------------------
    # Coherent-mode surface -- declared for static typing only
    # ------------------------------------------------------------------
    # These attributes exist only on ``_CoherentSyntheticReceiver`` instances
    # returned by ``__new__`` when ``scenario.array`` is set. Declaring them
    # here under ``TYPE_CHECKING`` lets test code call them without a static-
    # type error while keeping the runtime ``isinstance(.., CoherentReceiver)``
    # check honest: a plain ``SyntheticReceiver`` instance still lacks the
    # attributes at runtime, so the Protocol check returns False on
    # single-channel scenarios. Callers MUST guard with isinstance before
    # calling these methods on a non-coherent instance.
    if TYPE_CHECKING:

        def read_coherent(self, n_samples: int) -> np.ndarray: ...

        def calibrate(self) -> None: ...

        @property
        def is_calibrated(self) -> bool: ...

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _emitter_baseband(self, emitter_index: int, n_samples: int) -> np.ndarray:
        """Render one emitter's post-channel complex baseband samples.

        Returns ``(n_samples,)`` ``complex128`` containing the antenna-gain-
        weighted, free-space-attenuated, sample-aligned baseband contribution
        of the emitter at index ``emitter_index``. The array-local geometry
        (steering phases for the coherent path) is applied by the caller, not
        here -- this method delivers the same scalar baseband for every channel.
        """
        scn = self._scenario
        emitter = scn.emitters[emitter_index]
        sample_rate_hz = scn.sample_rate_hz
        t = (self._sample_offset + np.arange(n_samples, dtype=np.float64)) / sample_rate_hz
        delta_deg = _wrap_pm180(emitter.azimuth_deg - self._heading_deg)
        gain_linear = scn.antenna.gain_linear(delta_deg)
        tx_amplitude = 10.0 ** (emitter.tx_power_db / _DB_AMPLITUDE_DIVISOR)
        freq_offset_hz = emitter.frequency_hz - scn.center_freq_hz
        phase = math.tau * freq_offset_hz * t + math.radians(emitter.phase_deg)
        base = tx_amplitude * math.sqrt(gain_linear) * np.exp(1j * phase)
        propagated: np.ndarray = scn.channel.apply(
            base,
            emitter.range_m,
            emitter.frequency_hz,
            self._rng.noise,
        )
        return propagated.astype(np.complex128)

    def _render_block_single(self, n_samples: int) -> np.ndarray:
        """Render the next ``n_samples`` complex64 samples for the L1 single-channel path.

        ``self._sample_offset`` is *not* incremented here; the caller
        (``read``) does that after we return, so an in-flight render
        that raises does not corrupt the counter.
        """
        scn = self._scenario
        signal = np.zeros(n_samples, dtype=np.complex128)
        for emitter_index in range(len(scn.emitters)):
            signal += self._emitter_baseband(emitter_index, n_samples)
        noise = complex_awgn(self._rng.noise, n_samples, scn.noise_floor_dbfs)
        out: np.ndarray = (signal + noise).astype(np.complex64)
        return out


class _CoherentSyntheticReceiver(SyntheticReceiver):
    """Coherent multi-channel synthetic receiver. Private; constructed via ``SyntheticReceiver``.

    Exposes the ``CoherentReceiver`` Protocol surface -- ``read_coherent``,
    ``calibrate``, ``is_calibrated`` -- on top of the base ``Receiver``
    surface. The base ``read(n)`` is overridden to return row 0 of an
    internally rendered coherent block (so RNG consumption is consistent
    with ``read_coherent`` -- Acceptance 1(d)).
    """

    def __new__(
        cls,
        scenario: SimulationScenario,
        seed: int = 0,
    ) -> _CoherentSyntheticReceiver:
        """Direct construction bypasses the base-class factory check."""
        del scenario, seed  # consumed by __init__, ignored here
        return object.__new__(cls)

    def __init__(self, scenario: SimulationScenario, seed: int = 0) -> None:
        """Initialise the base receiver and the calibration state."""
        super().__init__(scenario, seed=seed)
        if scenario.array is None:
            msg = "_CoherentSyntheticReceiver requires scenario.array to be set."
            raise ValueError(msg)
        self._is_calibrated_state: bool = False
        self._calibration: Calibration | None = None

    # ------------------------------------------------------------------
    # CoherentReceiver Protocol surface
    # ------------------------------------------------------------------

    def read_coherent(self, n_samples: int) -> np.ndarray:
        """Return ``(n_channels, n_samples)`` complex64 of sample-aligned coherent IQ.

        Same blocking / no-silent-short-read contract as ``read``. Rows are
        phase-coherent per the last ``calibrate()``; calling this before a
        successful ``calibrate()`` is allowed (no raise) but ``is_calibrated``
        is False and the L2 estimator must refuse to emit on the resulting
        block.
        """
        if not self._is_open:
            msg = "SyntheticReceiver.read_coherent() called before open() (or after close())."
            raise ReceiverNotOpenError(msg)
        if n_samples <= 0:
            msg = f"SyntheticReceiver.read_coherent(n) requires n > 0 (got {n_samples})."
            raise InvalidReadSizeError(msg)
        samples = self._render_block_coherent(n_samples)
        self._sample_offset += n_samples
        return samples

    def read(self, n_samples: int) -> np.ndarray:
        """Return exactly ``n_samples`` of complex64 single-channel IQ (channel 0).

        On a coherent instance, ``read`` renders the full coherent block and
        returns row 0. This keeps RNG consumption identical to
        ``read_coherent``, so Acceptance 1(d) -- ``read(n)`` byte-equal to
        ``read_coherent(n)[0, :]`` at matched seed -- holds.
        """
        if not self._is_open:
            msg = "SyntheticReceiver.read() called before open() (or after close())."
            raise ReceiverNotOpenError(msg)
        if n_samples <= 0:
            msg = f"SyntheticReceiver.read(n) requires n > 0 (got {n_samples})."
            raise InvalidReadSizeError(msg)
        block = self._render_block_coherent(n_samples)
        self._sample_offset += n_samples
        # ``block[0, :]`` is a view into a complex64 buffer; copy so the caller
        # cannot mutate the receiver's internal scratch (and so the returned
        # array is a contiguous 1-D buffer regardless of stride).
        return np.ascontiguousarray(block[0, :])

    def calibrate(self) -> None:
        """Run the simulated noise-source calibration handshake.

        See the module docstring for the full model. On success
        ``is_calibrated`` becomes True; on insufficient SNR
        ``CalibrationFailedError`` is raised and ``is_calibrated`` stays
        False.
        """
        scn = self._scenario
        array = scn.array
        if array is None:
            msg = (
                "_CoherentSyntheticReceiver.calibrate: scenario.array is None; "
                "this should be unreachable -- raised defensively."
            )
            raise RuntimeError(msg)

        n_samples = _CALIBRATION_N_SAMPLES
        ref_snr_db = scn.calibration_reference_snr_db

        # Per-channel ADC noise amplitude (sigma_complex = 10**(dbfs/20)).
        sigma_adc = 10.0 ** (scn.noise_floor_dbfs / _DB_AMPLITUDE_DIVISOR)
        # Reference noise amplitude derived from the configured SNR over the
        # ADC noise floor. SNR_db is a power ratio -> amplitude ratio is half.
        ref_amplitude_factor = 10.0 ** (ref_snr_db / _DB_AMPLITUDE_DIVISOR)
        sigma_ref = sigma_adc * ref_amplitude_factor

        rng_cal = self._rng.cal_noise

        # The single noise realisation broadcast to every channel.
        common_real = rng_cal.standard_normal(n_samples) * (sigma_ref / math.sqrt(2.0))
        common_imag = rng_cal.standard_normal(n_samples) * (sigma_ref / math.sqrt(2.0))
        common_noise = common_real + 1j * common_imag

        # Per-channel impairments live between the noise source and the ADC,
        # so they are applied *to the common noise* before per-channel ADC
        # noise is added. With no impairments configured the offsets reduce
        # to identity.
        if scn.impairments is not None:
            channel_offsets = scn.impairments.channel_offsets
        else:
            channel_offsets = np.ones(array.n_elements, dtype=np.complex128)

        cal_data = np.empty((array.n_elements, n_samples), dtype=np.complex128)
        for i in range(array.n_elements):
            cal_data[i, :] = common_noise * channel_offsets[i]

        # ADC noise: independent per channel. Use the cal_noise stream so
        # main-stream reproducibility is unaffected by a calibrate() event.
        adc_noise = complex_awgn_2d(rng_cal, array.n_elements, n_samples, scn.noise_floor_dbfs)
        cal_data += adc_noise

        # Cross-channel correlations R_i = <x_i * conj(x_0)>.
        # axis=1 reduces along samples, yielding shape (n_elements,).
        r_i = np.mean(cal_data * np.conj(cal_data[0, :]), axis=1)
        # R_0 = <|x_0|^2> = signal_power * |offset_0|^2 + adc_noise_power.
        # With offset_0 == 1 by convention and a known noise floor, signal
        # power is R_0 - noise_power. Using the *known* noise power (rather
        # than estimating it from |R_i| for i > 0) keeps the signal-power
        # estimate unbiased by the impairments -- the source of the 0.2 dB
        # systematic error if mean(|offset_i|) is used as the scale.
        adc_noise_power = sigma_adc**2
        signal_power_est = max(float(np.abs(r_i[0])) - adc_noise_power, _SIGNAL_POWER_FLOOR)
        noise_power_est = max(adc_noise_power, _SIGNAL_POWER_FLOOR)
        measured_snr_db = 10.0 * math.log10(signal_power_est / noise_power_est)

        if measured_snr_db < _MIN_CALIBRATION_SNR_DB:
            msg = (
                f"Calibration handshake measured SNR {measured_snr_db:.2f} dB "
                f"below required threshold {_MIN_CALIBRATION_SNR_DB:.1f} dB. "
                "The simulated noise reference is too weak to recover stable "
                "inter-channel offsets; raise scenario.calibration_reference_snr_db."
            )
            raise CalibrationFailedError(msg)

        # measured_offset_i ~ R_i / signal_power. The bias (signal_power_est
        # carries mean |offset_i|, not 1.0) cancels in inter-channel ratios,
        # which is what the L2 estimator and Acceptance 1(j) measure.
        measured_offsets = r_i / signal_power_est
        # Channel 0 is the reference by convention -- pin it exactly.
        measured_offsets[0] = 1.0 + 0.0j
        correction = 1.0 / measured_offsets
        correction[0] = 1.0 + 0.0j

        self._calibration = Calibration(complex_offsets=correction.astype(np.complex128))
        self._is_calibrated_state = True

    @property
    def is_calibrated(self) -> bool:
        """Whether a successful ``calibrate()`` is currently in effect."""
        return self._is_calibrated_state

    # ------------------------------------------------------------------
    # Receiver lifecycle overrides
    # ------------------------------------------------------------------

    def configure(self, config: NodeConfig) -> None:
        """Record the node config and invalidate any existing calibration.

        A retune randomises inter-channel phase relationships on real
        coherent SDRs; the simulator honestly mirrors that even though
        impairments here are scenario-static. The stored ``Calibration``
        record is kept (the operator can re-arm without re-rendering)
        but ``is_calibrated`` returns False until ``calibrate()`` runs
        again.
        """
        super().configure(config)
        self._is_calibrated_state = False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _render_block_coherent(self, n_samples: int) -> np.ndarray:
        """Render the next ``n_samples`` complex64 samples across all channels.

        Per-emitter contribution per channel is
        ``base * exp(j * steering_phase_i) * impairment_i * correction_i``,
        where ``correction_i`` is the calibration inverse offset when
        calibrated and identity otherwise. Per-channel ADC noise is added
        last from the main noise stream.
        """
        scn = self._scenario
        array = scn.array
        # The Pydantic-like cross-validation in scenario.__post_init__
        # ensures array is set when we reach here, but mypy needs the local
        # narrowing.
        if array is None:
            msg = "_render_block_coherent invoked with scenario.array = None."
            raise RuntimeError(msg)

        n_channels = array.n_elements
        per_channel = np.zeros((n_channels, n_samples), dtype=np.complex128)

        impairment_offsets = (
            scn.impairments.channel_offsets
            if scn.impairments is not None
            else np.ones(n_channels, dtype=np.complex128)
        )
        if self._is_calibrated_state and self._calibration is not None:
            correction_offsets = self._calibration.complex_offsets
        else:
            correction_offsets = np.ones(n_channels, dtype=np.complex128)
        # Per-channel combined gain that does not depend on emitter; computed
        # once and broadcast along the sample axis below.
        per_channel_gain = (impairment_offsets * correction_offsets).astype(np.complex128)

        for emitter_index, emitter in enumerate(scn.emitters):
            delta_local_deg = _wrap_pm180(emitter.azimuth_deg - self._heading_deg)
            delta_local_rad = math.radians(delta_local_deg)
            wavelength_m = _SPEED_OF_LIGHT_M_PER_S / emitter.frequency_hz
            steering_phases = array.steering_phases(delta_local_rad, wavelength_m)
            steering_vec = np.exp(1j * steering_phases)  # shape (n_channels,)
            base = self._emitter_baseband(emitter_index, n_samples)
            per_channel += (per_channel_gain * steering_vec)[:, np.newaxis] * base[np.newaxis, :]

        adc_noise = complex_awgn_2d(self._rng.noise, n_channels, n_samples, scn.noise_floor_dbfs)
        out: np.ndarray = (per_channel + adc_noise).astype(np.complex64)
        return out
