"""Array manifold: far-field steering vectors and matrices for ULA/UCA/CUSTOM arrays.

This module is the *manifold* substrate of L2 subspace direction-finding. Given
an angle theta, a wavelength lambda, and an array geometry, it produces a
unit-norm complex steering vector ``a(theta, lambda) in C^N`` whose i-th entry
is the relative phase that array element i sees from a far-field source in
direction theta. MUSIC and MVDR (future tickets WS-B-003 / WS-B-004) consume
this manifold together with a sample-covariance matrix; this module does not
estimate any angles.

ARRAY-LOCAL COORDINATE CONVENTION
---------------------------------
Binding -- matches ``rfmesh_sdr.simulator.array.ArraySpec`` byte-for-byte so
every L2 test that pipes simulator IQ through MUSIC sees a consistent
manifold:

* Boresight = +x axis. ``azimuth_rad = 0`` means the source is in the +x
  direction (broadside for a ULA aligned on the y-axis).
* Source-direction unit vector: ``k_hat = (cos theta, sin theta)``.
* ULA: elements on the y-axis at ``(0, i * d)`` for ``i = 0 .. N-1``.
  Inter-element phase = ``-2 pi (d / lambda) sin(theta)`` per index step.
* UCA: elements on a circle of radius r around the origin, channel 0 at
  ``(r, 0)``, element i at ``(r cos alpha_i, r sin alpha_i)`` with
  ``alpha_i = 2 pi i / N``. Per-element phase
  = ``-2 pi (r / lambda) cos(theta - alpha_i)``.
* CUSTOM: explicit (x, y) positions; per-element phase
  = ``-2 pi <p_i, k_hat> / lambda``.

The negative sign of the exponent is the standard receive-array convention
for an incoming plane wave (the wavefront reaches an element further along
the source direction *later*, so its phase trails by the path delta).
"""

from __future__ import annotations

import math

import numpy as np
from rfmesh_contracts import ArrayGeometry  # type: ignore[import-untyped, unused-ignore]

_DIM = 2
_TWO_PI = 2.0 * math.pi


def _validate_positions(element_positions_m: np.ndarray) -> None:
    if not isinstance(element_positions_m, np.ndarray):
        msg = (
            "element_positions_m must be a numpy.ndarray of shape (N, 2); got "
            f"{type(element_positions_m).__name__}."
        )
        raise TypeError(msg)
    if element_positions_m.ndim != _DIM or element_positions_m.shape[1] != _DIM:
        msg = f"element_positions_m must have shape (N, 2); got {element_positions_m.shape}."
        raise ValueError(msg)
    if element_positions_m.dtype != np.float64:
        msg = f"element_positions_m must be dtype float64; got {element_positions_m.dtype}."
        raise ValueError(msg)
    if element_positions_m.shape[0] < _DIM:
        msg = (
            "element_positions_m must describe N >= 2 elements; got "
            f"{element_positions_m.shape[0]}."
        )
        raise ValueError(msg)


def _validate_wavelength(wavelength_m: float) -> None:
    if not math.isfinite(wavelength_m) or wavelength_m <= 0.0:
        msg = f"wavelength_m must be a positive finite number; got {wavelength_m}."
        raise ValueError(msg)


def _validate_geometry(geometry: ArrayGeometry) -> None:
    if not isinstance(geometry, ArrayGeometry):
        msg = f"geometry must be an ArrayGeometry enum value; got {type(geometry).__name__}."
        raise TypeError(msg)


def steering_vector(
    *,
    geometry: ArrayGeometry,
    element_positions_m: np.ndarray,
    azimuth_rad: float,
    wavelength_m: float,
) -> np.ndarray:
    """Return the far-field unit-norm steering vector for one direction.

    The i-th entry is
    ``exp(-1j * 2 pi * <p_i, k_hat> / lambda) / sqrt(N)`` where
    ``k_hat = (cos azimuth, sin azimuth)``. The vector is complex128 and
    has L2 norm 1.

    Args:
        geometry: Array layout tag. Informational at v0.1 -- the per-element
            phase is computed from ``element_positions_m`` for all three
            geometries because the formula is the same dot product. Carried in
            the signature so callers state their intent and so a future
            specialisation (e.g. factored ULA progression) can plug in
            without breaking the API.
        element_positions_m: ``(N, 2) float64`` array of (x, y) element
            positions in metres in the array-local frame. Must follow the
            convention documented in the module docstring.
        azimuth_rad: Source azimuth in the array-local frame, radians.
            Broadside (+x) is 0; the +y direction is +pi/2.
        wavelength_m: Carrier wavelength in metres, strictly positive.

    Returns:
        ``(N,) complex128`` unit-norm steering vector.

    Raises:
        TypeError: If ``geometry`` is not an ``ArrayGeometry`` or
            ``element_positions_m`` is not a ``numpy.ndarray``.
        ValueError: If positions are malformed or ``wavelength_m`` is
            non-positive / non-finite.
    """
    _validate_geometry(geometry)
    _validate_positions(element_positions_m)
    _validate_wavelength(wavelength_m)
    n_elements = element_positions_m.shape[0]
    k_hat = np.array(
        [math.cos(azimuth_rad), math.sin(azimuth_rad)],
        dtype=np.float64,
    )
    path_delta = element_positions_m @ k_hat  # shape (N,)
    phases = -_TWO_PI * path_delta / wavelength_m
    vector: np.ndarray = np.exp(1j * phases.astype(np.complex128))
    vector /= math.sqrt(n_elements)
    return vector


def steering_matrix(
    *,
    geometry: ArrayGeometry,
    element_positions_m: np.ndarray,
    azimuths_rad: np.ndarray,
    wavelength_m: float,
) -> np.ndarray:
    """Return ``(N, K)`` stack of unit-norm steering vectors for K directions.

    Column ``k`` is ``steering_vector(geometry=..., azimuth_rad=azimuths_rad[k],
    ...)``. Useful for MUSIC pseudospectrum scanning and MVDR beam pattern
    plotting.

    Args:
        geometry: Array layout tag (informational, as in ``steering_vector``).
        element_positions_m: ``(N, 2) float64`` element positions.
        azimuths_rad: ``(K,) float64`` array of array-local azimuths.
        wavelength_m: Carrier wavelength, strictly positive.

    Returns:
        ``(N, K) complex128`` matrix; each column is unit-norm.

    Raises:
        TypeError: If ``geometry`` is not an ``ArrayGeometry`` or
            ``azimuths_rad`` is not a ``numpy.ndarray``.
        ValueError: If shapes are malformed or ``wavelength_m`` is invalid.
    """
    _validate_geometry(geometry)
    _validate_positions(element_positions_m)
    _validate_wavelength(wavelength_m)
    if not isinstance(azimuths_rad, np.ndarray):
        msg = (
            "azimuths_rad must be a numpy.ndarray of shape (K,); got "
            f"{type(azimuths_rad).__name__}."
        )
        raise TypeError(msg)
    if azimuths_rad.ndim != 1:
        msg = f"azimuths_rad must be 1-D; got shape {azimuths_rad.shape}."
        raise ValueError(msg)
    n_elements = element_positions_m.shape[0]
    azimuths = azimuths_rad.astype(np.float64, copy=False)
    # Stack k_hat as a (2, K) matrix so positions @ k_hats yields (N, K),
    # exactly the column-wise generalisation of the single-vector dot product.
    k_hats = np.vstack((np.cos(azimuths), np.sin(azimuths)))
    path_delta = element_positions_m @ k_hats  # shape (N, K)
    phases = -_TWO_PI * path_delta / wavelength_m
    matrix: np.ndarray = np.exp(1j * phases.astype(np.complex128))
    matrix /= math.sqrt(n_elements)
    return matrix
