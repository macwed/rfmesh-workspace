"""Acceptance criterion 1(d): CompositeChannel layers channels orthogonally.

Two assertions:

* Composing channels via ``CompositeChannel`` produces the same output as
  applying them in sequence in code (within numerical precision).
* Multipath (deterministic FIR) and shadowing (one rng draw per call)
  commute under composition -- because shadowing reads a single sample
  before multiplying and multipath ignores rng, swapping their order in
  the composite chain leaves the same rng state at draw time, so the
  fade and the convolution combine identically.
"""

from __future__ import annotations

import numpy as np
from rfmesh_sdr import (
    CompositeChannel,
    FreeSpaceChannel,
    LogNormalShadowing,
    MultipathFIRChannel,
)

_DISTANCE_M = 1000.0
_FREQUENCY_HZ = 915e6
_N_SAMPLES = 1024
_NUMERIC_TOLERANCE = 1e-12


def _make_input(rng_seed: int) -> np.ndarray:
    """Synthesize a random complex baseband block to push through the channels."""
    rng = np.random.default_rng(rng_seed)
    real = rng.standard_normal(_N_SAMPLES)
    imag = rng.standard_normal(_N_SAMPLES)
    return (real + 1j * imag).astype(np.complex128)


def test_composite_channel_layers_orthogonally() -> None:
    """Composite matches in-line chaining, and FIR/shadowing commute under composition."""
    free_space = FreeSpaceChannel()
    multipath = MultipathFIRChannel(
        taps=np.array([1.0 + 0.0j, 0.3 * np.exp(1j * np.pi / 4.0)], dtype=np.complex128),
        delays=np.array([0, 20], dtype=np.int64),
    )
    shadowing = LogNormalShadowing(sigma_db=2.0)

    samples_in = _make_input(rng_seed=1)

    # ---- (i) Composite equals manual in-order chaining ----
    rng_manual = np.random.default_rng(42)
    out_manual = free_space.apply(samples_in, _DISTANCE_M, _FREQUENCY_HZ, rng_manual)
    out_manual = multipath.apply(out_manual, _DISTANCE_M, _FREQUENCY_HZ, rng_manual)
    out_manual = shadowing.apply(out_manual, _DISTANCE_M, _FREQUENCY_HZ, rng_manual)

    rng_composite = np.random.default_rng(42)
    composite = CompositeChannel(channels=(free_space, multipath, shadowing))
    out_composite = composite.apply(samples_in, _DISTANCE_M, _FREQUENCY_HZ, rng_composite)

    assert out_manual.shape == out_composite.shape
    assert np.allclose(out_manual, out_composite, atol=_NUMERIC_TOLERANCE), (
        "CompositeChannel result diverges from manual chaining beyond numerical precision."
    )

    # ---- (ii) FIR and shadowing commute (within numerical precision) ----
    # Shadowing draws one rng sample then multiplies; FIR ignores rng. In both
    # orderings the fade scalar and the linear FIR commute (scalar * conv(x, k)
    # == conv(scalar * x, k) for the same fade). The fade draw lands at the
    # same rng state because the deterministic channel in between consumes
    # zero rng samples.
    rng_a = np.random.default_rng(42)
    out_order_a = CompositeChannel(channels=(free_space, multipath, shadowing)).apply(
        samples_in, _DISTANCE_M, _FREQUENCY_HZ, rng_a
    )

    rng_b = np.random.default_rng(42)
    out_order_b = CompositeChannel(channels=(free_space, shadowing, multipath)).apply(
        samples_in, _DISTANCE_M, _FREQUENCY_HZ, rng_b
    )

    assert np.allclose(out_order_a, out_order_b, atol=_NUMERIC_TOLERANCE), (
        "Multipath and shadowing should commute under composition (same fade draw, "
        "linear FIR -- but the swap produced a different output)."
    )
