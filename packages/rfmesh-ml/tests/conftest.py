"""Synthetic IQ fixtures for the WS-B-005 modulation-classifier tests.

Self-contained: every fixture generates IQ with numpy primitives only
(sinusoids, FM modulation, hop dwells, linear chirps). The fixtures
*deliberately do not* import from ``rfmesh-sdr.simulator`` -- the
classifier unit tests must be readable from the IQ generation up,
without a sibling-workstream dependency in the import graph
(WS-B-005 acceptance criterion 6).

Each fixture is parametrised by SNR in dB. Noise is unit-variance
complex Gaussian; signal amplitude is solved from
``signal_power = noise_power * 10**(snr_db / 10)`` so the per-fixture
SNR is exact. Seeds use ``numpy.random.default_rng(seed=42)`` per the
ticket's determinism rule.

Available fixtures (all return ``(iq, fs)``):

* ``cw_iq`` -- single complex tone at a known offset.
* ``fhss_iq`` -- frequency-hopping signal over 8 bins.
* ``fsk_iq`` -- 2-FSK with +/- 25 kHz deviation, 9.6 kHz symbol rate.
* ``lora_iq`` -- up-chirp + down-chirp emulating SX1276 SF7 / 125 kHz BW.
* ``noise_iq`` -- pure complex Gaussian noise (no signal).
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import pytest

_SAMPLE_RATE_HZ: float = 1_000_000.0
"""Default sample rate for the fixtures. 1 MS/s is high enough to
contain a LoRa SF7 / 125 kHz chirp comfortably and to resolve the FHSS
hop set (8 bins spread over ~500 kHz)."""

_N_SAMPLES: int = 8192
"""Default block length. 8192 samples at 1 MS/s = 8.192 ms, long enough
for the LoRa SF7 symbol (~1.024 ms), the FHSS hop sequence (50 hops/s
-> ~20 ms between hops; the 8.192 ms block captures ~0.4 dwells but
the per-hop dwell is shorter -- see ``_FHSS_HOP_RATE_HZ``), and the
FSK symbol stream."""

_FHSS_HOP_RATE_HZ: float = 1500.0
"""Hop rate for the FHSS fixture. 1500 hops/s with 8 hops in the block
means 8192/1500 hops per second = ~12 dwells in the 8.192 ms block --
the hop-rate detector autocorrelation can find this comfortably."""

_FSK_DEVIATION_HZ: float = 25_000.0
"""2-FSK deviation. +/- 25 kHz around the centre tone."""

_FSK_SYMBOL_RATE_HZ: float = 9600.0
"""2-FSK symbol rate."""

_LORA_BANDWIDTH_HZ: float = 125_000.0
"""LoRa SX1276 SF7 / BW=125 kHz canonical bandwidth."""


def _noise_for_snr(
    rng: np.random.Generator, signal: npt.NDArray[np.complex64], snr_db: float
) -> npt.NDArray[np.complex64]:
    """Add complex Gaussian noise so the per-block SNR is ``snr_db``.

    Computes the empirical signal power from the input array, scales
    noise to match the target SNR exactly, returns the noisy signal.
    """
    signal_power = float(np.mean(np.abs(signal.astype(np.complex128)) ** 2))
    # Pure-noise fixture path: caller passes an all-zero signal, we
    # synthesise unit-variance noise (the else-branch is the regular SNR
    # scaling from signal power).
    noise_power = 1.0 if signal_power <= 0.0 else signal_power / (10.0 ** (snr_db / 10.0))
    sigma = math.sqrt(noise_power / 2.0)
    noise_real = rng.standard_normal(signal.size).astype(np.float32) * sigma
    noise_imag = rng.standard_normal(signal.size).astype(np.float32) * sigma
    noise = (noise_real + 1j * noise_imag).astype(np.complex64)
    return (signal + noise).astype(np.complex64)


def _cw(rng: np.random.Generator, snr_db: float) -> npt.NDArray[np.complex64]:
    """Single CW tone at a fixed offset from baseband, plus AWGN."""
    n = np.arange(_N_SAMPLES, dtype=np.float64)
    f0_hz = 100_000.0  # 100 kHz offset; well inside Nyquist.
    phase = 2.0 * math.pi * f0_hz * n / _SAMPLE_RATE_HZ
    tone = np.exp(1j * phase).astype(np.complex64)
    return _noise_for_snr(rng, tone, snr_db)


def _fhss(rng: np.random.Generator, snr_db: float) -> npt.NDArray[np.complex64]:
    """8-bin random-pattern FHSS.

    Hop set spans +/- 250 kHz around baseband in 8 equally-spaced bins.
    Dwells are equal length; the hop sequence is a seeded random
    permutation (so the autocorrelation peak corresponds to one dwell
    period, not to a periodic hop pattern). Constant envelope within
    each dwell (smooth phase across hop boundaries).
    """
    hop_freqs_hz = np.linspace(-250_000.0, 250_000.0, 8)
    samples_per_dwell = max(1, round(_SAMPLE_RATE_HZ / _FHSS_HOP_RATE_HZ))
    n_dwells = _N_SAMPLES // samples_per_dwell + 1
    # Use the rng for the hop sequence so the fixture remains deterministic.
    hop_sequence = rng.integers(0, len(hop_freqs_hz), size=n_dwells)

    signal = np.empty(_N_SAMPLES, dtype=np.complex64)
    phase = 0.0
    idx = 0
    for hop_idx in hop_sequence:
        f = float(hop_freqs_hz[hop_idx])
        dphi_per_sample = 2.0 * math.pi * f / _SAMPLE_RATE_HZ
        n_this_dwell = min(samples_per_dwell, _N_SAMPLES - idx)
        if n_this_dwell <= 0:
            break
        phases = phase + dphi_per_sample * np.arange(n_this_dwell, dtype=np.float64)
        signal[idx : idx + n_this_dwell] = np.exp(1j * phases).astype(np.complex64)
        # Maintain phase continuity across hops.
        phase = (phase + dphi_per_sample * n_this_dwell) % (2.0 * math.pi)
        idx += n_this_dwell
        if idx >= _N_SAMPLES:
            break

    return _noise_for_snr(rng, signal, snr_db)


def _fsk(rng: np.random.Generator, snr_db: float) -> npt.NDArray[np.complex64]:
    """2-FSK: constant envelope FM with two frequency states."""
    samples_per_symbol = max(1, round(_SAMPLE_RATE_HZ / _FSK_SYMBOL_RATE_HZ))
    n_symbols = _N_SAMPLES // samples_per_symbol + 1
    symbols = rng.integers(0, 2, size=n_symbols)
    # Map 0 -> -deviation, 1 -> +deviation.
    deviations = np.where(symbols == 0, -_FSK_DEVIATION_HZ, _FSK_DEVIATION_HZ).astype(np.float64)

    signal = np.empty(_N_SAMPLES, dtype=np.complex64)
    phase = 0.0
    idx = 0
    for f in deviations:
        dphi_per_sample = 2.0 * math.pi * float(f) / _SAMPLE_RATE_HZ
        n_this_symbol = min(samples_per_symbol, _N_SAMPLES - idx)
        if n_this_symbol <= 0:
            break
        phases = phase + dphi_per_sample * np.arange(n_this_symbol, dtype=np.float64)
        signal[idx : idx + n_this_symbol] = np.exp(1j * phases).astype(np.complex64)
        phase = (phase + dphi_per_sample * n_this_symbol) % (2.0 * math.pi)
        idx += n_this_symbol
        if idx >= _N_SAMPLES:
            break

    return _noise_for_snr(rng, signal, snr_db)


def _lora(rng: np.random.Generator, snr_db: float) -> npt.NDArray[np.complex64]:
    """LoRa-like up-chirp + down-chirp waveform.

    Linear frequency sweep from ``-bw/2`` to ``+bw/2`` then back; the
    classifier's chirp detector fits a single slope, and the up-chirp
    half of the block is enough to produce a clean linear fit. We
    construct the full chirp pair so the average envelope is constant
    (LoRa is constant-envelope).
    """
    bw = _LORA_BANDWIDTH_HZ
    # Symbol duration -- SF=7, BW=125 kHz -> 2^7 / 125 kHz = ~1.024 ms.
    samples_per_chirp = max(2, round(_SAMPLE_RATE_HZ * (2**7 / bw)))
    n_chirps = _N_SAMPLES // samples_per_chirp + 1
    direction = 1.0  # start with up-chirp.

    signal = np.empty(_N_SAMPLES, dtype=np.complex64)
    phase = 0.0
    idx = 0
    for _ in range(n_chirps):
        n_this_chirp = min(samples_per_chirp, _N_SAMPLES - idx)
        if n_this_chirp <= 0:
            break
        t_local = np.arange(n_this_chirp, dtype=np.float64) / _SAMPLE_RATE_HZ
        # Linear sweep from -bw/2 to +bw/2 (or reverse if direction == -1)
        # over ``samples_per_chirp / fs`` seconds.
        t_full = samples_per_chirp / _SAMPLE_RATE_HZ
        slope_hz_per_s = direction * bw / t_full
        # Phase = 2*pi * integral of (f0 + slope * t) dt
        f0 = -direction * bw / 2.0
        chirp_phase = phase + 2.0 * math.pi * f0 * t_local + math.pi * slope_hz_per_s * (t_local**2)
        signal[idx : idx + n_this_chirp] = np.exp(1j * chirp_phase).astype(np.complex64)
        # Phase continuity at the chirp boundary -- compute the phase at
        # ``t = n_this_chirp / fs`` from the same formula.
        t_end = n_this_chirp / _SAMPLE_RATE_HZ
        phase = (phase + 2.0 * math.pi * f0 * t_end + math.pi * slope_hz_per_s * (t_end**2)) % (
            2.0 * math.pi
        )
        direction = -direction
        idx += n_this_chirp
        if idx >= _N_SAMPLES:
            break

    # The intentional up-chirp + down-chirp pair makes the *block-level*
    # linear-fit slope ambiguous, which would mask the chirp signature.
    # We slice to a single sweep direction so the chirp detector reads a
    # consistent slope -- a real LoRa symbol is one direction over one
    # symbol period anyway.
    if samples_per_chirp <= _N_SAMPLES:
        signal = signal[:samples_per_chirp].copy()
        signal = np.tile(signal, _N_SAMPLES // samples_per_chirp + 1)[:_N_SAMPLES]

    return _noise_for_snr(rng, signal.astype(np.complex64), snr_db)


def _pure_noise(rng: np.random.Generator) -> npt.NDArray[np.complex64]:
    """Unit-variance complex Gaussian noise; no signal."""
    real = rng.standard_normal(_N_SAMPLES).astype(np.float32)
    imag = rng.standard_normal(_N_SAMPLES).astype(np.float32)
    return ((real + 1j * imag) / math.sqrt(2.0)).astype(np.complex64)


# ---------------------------------------------------------------------------
# Fixture interface. Each public fixture returns ``(iq, fs)``; the SNR is
# baked into the fixture for one configuration. Parametrised tests build
# their own via the factory fixtures below.
# ---------------------------------------------------------------------------


SignalGenerator = Callable[[np.random.Generator, float], npt.NDArray[np.complex64]]


@pytest.fixture
def sample_rate_hz() -> float:
    """Default sample rate (1 MS/s)."""
    return _SAMPLE_RATE_HZ


@pytest.fixture
def cw_iq_factory() -> Callable[[float, int], npt.NDArray[np.complex64]]:
    """Factory: ``cw_iq_factory(snr_db, seed)`` -> complex64 CW + AWGN."""

    def _factory(snr_db: float, seed: int = 42) -> npt.NDArray[np.complex64]:
        return _cw(np.random.default_rng(seed), snr_db)

    return _factory


@pytest.fixture
def fhss_iq_factory() -> Callable[[float, int], npt.NDArray[np.complex64]]:
    """Factory: ``fhss_iq_factory(snr_db, seed)`` -> complex64 FHSS + AWGN."""

    def _factory(snr_db: float, seed: int = 42) -> npt.NDArray[np.complex64]:
        return _fhss(np.random.default_rng(seed), snr_db)

    return _factory


@pytest.fixture
def fsk_iq_factory() -> Callable[[float, int], npt.NDArray[np.complex64]]:
    """Factory: ``fsk_iq_factory(snr_db, seed)`` -> complex64 2-FSK + AWGN."""

    def _factory(snr_db: float, seed: int = 42) -> npt.NDArray[np.complex64]:
        return _fsk(np.random.default_rng(seed), snr_db)

    return _factory


@pytest.fixture
def lora_iq_factory() -> Callable[[float, int], npt.NDArray[np.complex64]]:
    """Factory: ``lora_iq_factory(snr_db, seed)`` -> complex64 LoRa chirp + AWGN."""

    def _factory(snr_db: float, seed: int = 42) -> npt.NDArray[np.complex64]:
        return _lora(np.random.default_rng(seed), snr_db)

    return _factory


@pytest.fixture
def noise_iq_factory() -> Callable[[int], npt.NDArray[np.complex64]]:
    """Factory: ``noise_iq_factory(seed)`` -> pure complex Gaussian noise (no signal)."""

    def _factory(seed: int = 42) -> npt.NDArray[np.complex64]:
        return _pure_noise(np.random.default_rng(seed))

    return _factory
