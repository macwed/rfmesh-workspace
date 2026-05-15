"""Complex AWGN at a configurable dBFS noise floor.

The simulator's noise floor is specified in dBFS -- decibels relative to
the abstract "full-scale 1.0" amplitude. The absolute level is arbitrary
because SDRs are not power-calibrated (``INHERITED_CONTEXT.md`` Section 1.3);
only the ratio between the signal level (also on this arbitrary scale)
and the noise floor is physically meaningful, and that ratio is exactly
the SNR the L1 estimator sees.

For a baseband complex sample, AWGN is circularly symmetric Gaussian:
real and imaginary parts each Normal(0, sigma/sqrt(2)), giving a total
complex variance of sigma**2. sigma is derived from the dBFS floor as
sigma = 10**(noise_floor_dbfs / 20).
"""

from __future__ import annotations

import math

import numpy as np

# Decibels are 20*log10 for voltage/amplitude and 10*log10 for power.
# dBFS is conventionally an amplitude-side measure: 0 dBFS = full-scale
# amplitude 1.0, so 10**(dBFS/20) is the linear amplitude.
_DB_AMPLITUDE_DIVISOR = 20.0
_COMPLEX_AWGN_VARIANCE_SPLIT = math.sqrt(2.0)


def complex_awgn(
    rng: np.random.Generator,
    n_samples: int,
    noise_floor_dbfs: float,
) -> np.ndarray:
    """Return ``n_samples`` of complex-128 circularly-symmetric AWGN.

    Output dtype is ``complex128`` so the caller (SyntheticReceiver) can
    sum it into a complex-128 signal accumulator with full precision before
    a single cast to complex64 at the boundary.
    """
    if n_samples <= 0:
        msg = f"complex_awgn requires n_samples > 0 (got {n_samples})."
        raise ValueError(msg)
    sigma = 10.0 ** (noise_floor_dbfs / _DB_AMPLITUDE_DIVISOR)
    scale = sigma / _COMPLEX_AWGN_VARIANCE_SPLIT
    real = rng.standard_normal(n_samples) * scale
    imag = rng.standard_normal(n_samples) * scale
    out: np.ndarray = (real + 1j * imag).astype(np.complex128)
    return out


def complex_awgn_2d(
    rng: np.random.Generator,
    n_channels: int,
    n_samples: int,
    noise_floor_dbfs: float,
) -> np.ndarray:
    """Return ``(n_channels, n_samples)`` of independent complex-128 AWGN.

    Each row is an independent draw from the same complex circularly-symmetric
    Gaussian as ``complex_awgn``. RNG consumption is a single bulk draw of
    ``2 * n_channels * n_samples`` standard normals (the real and imaginary
    halves), so per-channel statistical independence comes from the underlying
    SeedSequence-spawned Generator, not from running multiple generators.
    """
    if n_channels <= 0:
        msg = f"complex_awgn_2d requires n_channels > 0 (got {n_channels})."
        raise ValueError(msg)
    if n_samples <= 0:
        msg = f"complex_awgn_2d requires n_samples > 0 (got {n_samples})."
        raise ValueError(msg)
    sigma = 10.0 ** (noise_floor_dbfs / _DB_AMPLITUDE_DIVISOR)
    scale = sigma / _COMPLEX_AWGN_VARIANCE_SPLIT
    real = rng.standard_normal((n_channels, n_samples)) * scale
    imag = rng.standard_normal((n_channels, n_samples)) * scale
    out: np.ndarray = (real + 1j * imag).astype(np.complex128)
    return out
