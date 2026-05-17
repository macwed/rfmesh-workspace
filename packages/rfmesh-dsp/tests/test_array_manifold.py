"""WS-B-002 manifold tests: closed-form anchors plus a simulator cross-check.

Each test names exactly the property it pins. Closed-form anchors are
constructed inline -- no fixture sharing, so a failure points immediately
at the broken property without spelunking ``conftest.py``.

The simulator cross-check (``test_ula_matches_simulator_convention``) is the
binding convention test: every L2 fixture that pipes simulator IQ through
this module's manifold must agree with the simulator's own steering-phase
formula. If that test fails, do NOT patch one side; per the ticket Stop
condition, raise it with the lead -- the mismatch is either a sign error in
this module or a misread of the simulator's array-local convention, and
both need review.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    ArrayGeometry,
)
from rfmesh_dsp.array_manifold import steering_matrix, steering_vector

_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_REFERENCE_FREQ_HZ = 915e6
_REFERENCE_WAVELENGTH_M = _SPEED_OF_LIGHT_M_PER_S / _REFERENCE_FREQ_HZ
_TIGHT_TOLERANCE = 1e-12


def _ula_positions(n_elements: int, spacing_m: float) -> np.ndarray:
    positions = np.zeros((n_elements, 2), dtype=np.float64)
    positions[:, 1] = np.arange(n_elements, dtype=np.float64) * spacing_m
    return positions


def _uca_positions(n_elements: int, radius_m: float) -> np.ndarray:
    alphas = 2.0 * np.pi * np.arange(n_elements, dtype=np.float64) / n_elements
    positions = np.empty((n_elements, 2), dtype=np.float64)
    positions[:, 0] = radius_m * np.cos(alphas)
    positions[:, 1] = radius_m * np.sin(alphas)
    return positions


# ---------------------------------------------------------------------------
# Closed-form anchors -- the maths checks
# ---------------------------------------------------------------------------


def test_ula_broadside_is_ones_over_sqrt_n() -> None:
    """At broadside (theta = 0), every ULA element sees zero path delta.

    The ULA lies on the y-axis; ``k_hat = (1, 0)`` at theta=0 has no
    component along y; ``positions @ k_hat == 0`` for every element. So
    every phase is 0, every exp(...) is 1, and the unit-norm vector is
    ``ones / sqrt(N)``.
    """
    n = 4
    spacing_m = 0.5 * _REFERENCE_WAVELENGTH_M
    positions = _ula_positions(n, spacing_m)
    a = steering_vector(
        geometry=ArrayGeometry.ULA,
        element_positions_m=positions,
        azimuth_rad=0.0,
        wavelength_m=_REFERENCE_WAVELENGTH_M,
    )
    expected = np.ones(n, dtype=np.complex128) / math.sqrt(n)
    np.testing.assert_allclose(a, expected, atol=_TIGHT_TOLERANCE)


def test_ula_endfire_phase_progression() -> None:
    """At endfire (theta = pi/2, source along +y), phase i = -2 pi (d/lambda) i.

    For a ULA on the y-axis with element i at ``(0, i*d)`` and
    ``k_hat = (0, 1)``: ``<p_i, k_hat> = i * d``, so the steering vector is
    ``exp(-1j 2 pi (d/lambda) i) / sqrt(N)`` -- the classic uniform-progressive
    phase shift that MUSIC keys off.
    """
    n = 8
    spacing_m = 0.5 * _REFERENCE_WAVELENGTH_M
    positions = _ula_positions(n, spacing_m)
    a = steering_vector(
        geometry=ArrayGeometry.ULA,
        element_positions_m=positions,
        azimuth_rad=math.pi / 2.0,
        wavelength_m=_REFERENCE_WAVELENGTH_M,
    )
    expected_phases = (
        -2.0 * math.pi * (spacing_m / _REFERENCE_WAVELENGTH_M) * np.arange(n, dtype=np.float64)
    )
    expected = np.exp(1j * expected_phases.astype(np.complex128)) / math.sqrt(n)
    np.testing.assert_allclose(a, expected, atol=_TIGHT_TOLERANCE)


def test_ula_matches_simulator_convention() -> None:
    """Phases match ``rfmesh_sdr.ArraySpec.ula(n, d).steering_phases(delta, lambda)``.

    The simulator and the manifold compute the same dot product
    (positions @ k_hat) the same way; the cross-check pins the
    array-local convention (ULA on y-axis, k_hat = (cos, sin)) byte-for-byte.
    Tests-only import per the ticket; production DSP code must never touch
    rfmesh_sdr.
    """
    from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
        ArraySpec,
    )

    n = 4
    spacing_m = 0.5 * _REFERENCE_WAVELENGTH_M
    spec = ArraySpec.ula(n_elements=n, spacing_m=spacing_m)
    delta_rad = math.radians(35.0)
    # ArraySpec.element_positions_m is set read-only; copy to keep the test
    # cleanly decoupled from upstream mutation.
    positions = np.array(spec.element_positions_m, dtype=np.float64, copy=True)
    a = steering_vector(
        geometry=ArrayGeometry.ULA,
        element_positions_m=positions,
        azimuth_rad=delta_rad,
        wavelength_m=_REFERENCE_WAVELENGTH_M,
    )
    sim_phases = spec.steering_phases(delta_rad, _REFERENCE_WAVELENGTH_M)
    expected = np.exp(1j * sim_phases.astype(np.complex128)) / math.sqrt(n)
    # Byte-equal: same float64 operations on both sides.
    np.testing.assert_array_equal(a, expected)


def test_uca_cyclic_symmetry() -> None:
    """A rotation by alpha_k = 2 pi k / N permutes the UCA steering vector cyclically.

    ``a_UCA(alpha_k)[i] = a_UCA(0)[(i - k) mod N]``: rotating the source by
    one ring-element step shifts the steering vector by one channel. This is
    a stronger property than mere rotational symmetry -- the *exact* complex
    values reappear, just rolled.
    """
    n = 6
    radius_m = 0.5 * _REFERENCE_WAVELENGTH_M
    positions = _uca_positions(n, radius_m)
    a_at_zero = steering_vector(
        geometry=ArrayGeometry.UCA,
        element_positions_m=positions,
        azimuth_rad=0.0,
        wavelength_m=_REFERENCE_WAVELENGTH_M,
    )
    ring_step_rad = 2.0 * math.pi / n
    for k in range(n):
        a_at_k = steering_vector(
            geometry=ArrayGeometry.UCA,
            element_positions_m=positions,
            azimuth_rad=k * ring_step_rad,
            wavelength_m=_REFERENCE_WAVELENGTH_M,
        )
        # np.roll(a, k) yields out[i] = a[(i - k) mod N] -- exactly the
        # cyclic shift the symmetry predicts.
        expected = np.roll(a_at_zero, k)
        np.testing.assert_allclose(a_at_k, expected, atol=_TIGHT_TOLERANCE)


def test_custom_geometry_round_trip() -> None:
    """Random (N, 2) positions: ``steering_matrix`` cols are unit-norm and match hand-derivation."""
    rng = np.random.default_rng(20260515)
    n = 5
    k_azimuths = 9
    positions = (rng.standard_normal((n, 2)) * 0.1).astype(np.float64)
    azimuths_rad = np.linspace(
        -math.pi,
        math.pi,
        k_azimuths,
        endpoint=False,
        dtype=np.float64,
    )
    manifold = steering_matrix(
        geometry=ArrayGeometry.CUSTOM,
        element_positions_m=positions,
        azimuths_rad=azimuths_rad,
        wavelength_m=_REFERENCE_WAVELENGTH_M,
    )
    assert manifold.shape == (n, k_azimuths)
    assert manifold.dtype == np.complex128
    norms = np.linalg.norm(manifold, axis=0)
    np.testing.assert_allclose(norms, np.ones(k_azimuths), atol=_TIGHT_TOLERANCE)

    for col_idx, theta in enumerate(azimuths_rad):
        k_hat = np.array([math.cos(theta), math.sin(theta)], dtype=np.float64)
        path_delta = positions @ k_hat
        expected_phases = -2.0 * math.pi * path_delta / _REFERENCE_WAVELENGTH_M
        expected = np.exp(1j * expected_phases.astype(np.complex128)) / math.sqrt(n)
        np.testing.assert_allclose(manifold[:, col_idx], expected, atol=_TIGHT_TOLERANCE)


def test_steering_vector_validates_inputs() -> None:
    """Bad inputs raise loudly (Invariant 4: no silent fallbacks)."""
    good_positions = _ula_positions(4, 0.1)
    with pytest.raises(ValueError, match="wavelength_m"):
        steering_vector(
            geometry=ArrayGeometry.ULA,
            element_positions_m=good_positions,
            azimuth_rad=0.0,
            wavelength_m=0.0,
        )
    with pytest.raises(ValueError, match="shape"):
        steering_vector(
            geometry=ArrayGeometry.ULA,
            element_positions_m=np.zeros((4, 3), dtype=np.float64),
            azimuth_rad=0.0,
            wavelength_m=_REFERENCE_WAVELENGTH_M,
        )
    with pytest.raises(ValueError, match="float64"):
        steering_vector(
            geometry=ArrayGeometry.ULA,
            element_positions_m=good_positions.astype(np.float32),
            azimuth_rad=0.0,
            wavelength_m=_REFERENCE_WAVELENGTH_M,
        )


# ---------------------------------------------------------------------------
# Golden-file checks (Invariant 3)
# ---------------------------------------------------------------------------


def test_golden_manifold_ula_8elem_half_lambda(golden_dir: Path) -> None:
    """Re-compute the 8-elem ULA manifold; max-abs diff vs committed golden <= 1e-12."""
    fixture = np.load(golden_dir / "manifold_ula_8elem_half_lambda.npz")
    positions = fixture["positions"]
    azimuths_rad = fixture["azimuths_rad"]
    wavelength_m = float(fixture["wavelength_m"])
    expected = fixture["manifold"]
    actual = steering_matrix(
        geometry=ArrayGeometry.ULA,
        element_positions_m=positions,
        azimuths_rad=azimuths_rad,
        wavelength_m=wavelength_m,
    )
    assert actual.shape == expected.shape == (8, 361)
    assert actual.dtype == expected.dtype == np.complex128
    max_abs_diff = float(np.max(np.abs(actual - expected)))
    assert max_abs_diff <= _TIGHT_TOLERANCE, f"ULA manifold drift: max-abs diff {max_abs_diff:.3e}"


def test_golden_manifold_uca_6elem_quarter_lambda(golden_dir: Path) -> None:
    """Re-compute the 6-elem UCA manifold; max-abs diff vs committed golden <= 1e-12."""
    fixture = np.load(golden_dir / "manifold_uca_6elem_quarter_lambda.npz")
    positions = fixture["positions"]
    azimuths_rad = fixture["azimuths_rad"]
    wavelength_m = float(fixture["wavelength_m"])
    expected = fixture["manifold"]
    actual = steering_matrix(
        geometry=ArrayGeometry.UCA,
        element_positions_m=positions,
        azimuths_rad=azimuths_rad,
        wavelength_m=wavelength_m,
    )
    assert actual.shape == expected.shape == (6, 361)
    assert actual.dtype == expected.dtype == np.complex128
    max_abs_diff = float(np.max(np.abs(actual - expected)))
    assert max_abs_diff <= _TIGHT_TOLERANCE, f"UCA manifold drift: max-abs diff {max_abs_diff:.3e}"
