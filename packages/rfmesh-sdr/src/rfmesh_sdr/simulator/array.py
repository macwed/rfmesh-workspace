"""``ArraySpec`` -- the simulator's frozen description of a phase-coherent array.

WS-A-002 introduces multi-channel coherent rendering to ``SyntheticReceiver``.
``ArraySpec`` is the data-only object that ``SimulationScenario`` carries when
the scenario models an L2 (phase-DF) deployment; the receiver consumes it to
compute the per-channel steering phases that the L2 MUSIC / MVDR estimator
will eventually invert.

ARRAY-LOCAL COORDINATE CONVENTION
---------------------------------
The simulator uses a 2-D array-local frame consistent across all geometries:

* **Boresight = +x axis.** ``delta_local = 0`` means the emitter is in the +x
  direction (the array's "front"). This pairs with the L1 antenna pattern
  convention (peak gain at delta_local = 0) and with the Decision 1
  resolution in the WS-A-002 ticket (array-local angle = emitter azimuth - heading).
* **Source-direction unit vector:** ``k_hat = (cos delta_local, sin delta_local)``.
* **ULA elements on the y-axis,** at ``(0, i * spacing_m)`` for i = 0 .. N-1.
  The array's antenna line is perpendicular to its boresight (broadside ULA).
  Cross-channel path delta projects onto k_hat as ``i * spacing * sin(delta_local)``,
  yielding the canonical broadside ULA steering phase
  ``-2 * pi * (d/lambda) * sin(delta_local)`` -- matching the binding test
  formula in WS-A-002 Acceptance 1(e).
* **UCA elements on a circle around origin,** at
  ``(r * cos(alpha_i), r * sin(alpha_i))`` with ``alpha_i = 2*pi*i/N``. Channel 0
  is at ``(r, 0)`` (alpha_0 = 0). Path delta projects as
  ``r * cos(delta_local - alpha_i)``, yielding the per-element steering phase
  ``-2 * pi * (r/lambda) * cos(delta_local - alpha_i)`` -- matching WS-A-002
  Acceptance 1(f).
* **CUSTOM positions** are user-supplied (x, y) tuples in metres; by convention
  channel 0 is placed at the origin, though this is not enforced (a non-symmetric
  layout test in 1(g) puts channel 0 at (0, 0) explicitly).

Why this and not the alternative (ULA on x-axis):
The ticket's Acceptance 1(e) expected formula uses ``sin(theta)`` for the
ULA's inter-element phase. With boresight = +x and ``k_hat = (cos, sin)``
(both fixed by the UCA test 1(f)), placing the ULA along x would give a
``cos(theta)`` formula -- the wrong one. Placing the ULA along y is the
only convention that satisfies both the UCA and ULA binding tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np
from rfmesh_contracts import ArrayGeometry  # type: ignore[import-untyped, unused-ignore]

_TWO = 2
_POSITION_TOLERANCE_M = 1e-9


@dataclass(frozen=True)
class ArraySpec:
    """Frozen, immutable description of the array's geometry and element positions.

    Use the ``ula``, ``uca``, and ``custom`` class-method factories rather
    than the raw constructor: they apply the convention-correct geometry tag
    and element positions, and they run ``__post_init__`` validation.

    Attributes:
        geometry: One of ``ArrayGeometry.{ULA, UCA, CUSTOM}``.
        n_elements: Number of coherent RX channels (>= 2).
        element_positions_m: Frozen ``(n_elements, 2)`` ``float64`` array of
            (x, y) element positions in metres, in the array-local frame.
            Row i corresponds to channel i.
    """

    geometry: ArrayGeometry
    n_elements: int
    element_positions_m: np.ndarray = field(repr=False)

    # ULA element-spacing-uniformity tolerance: a hand-built ULA via .custom()
    # might have rounding noise on its y coordinates. Used by the post-init
    # consistency check via ``__post_init__``; not part of the public API.
    _POSITION_TOLERANCE_M: ClassVar[float] = _POSITION_TOLERANCE_M

    def __post_init__(self) -> None:
        """Validate the array description against the geometry tag.

        Catches typo-class errors here so the receiver and the (eventual) DSP
        estimator can assume a well-formed array -- the same Invariant 4
        philosophy applied to the simulator-internal data layer.
        """
        if self.n_elements < _TWO:
            msg = f"ArraySpec requires n_elements >= 2 (got {self.n_elements})."
            raise ValueError(msg)
        if self.element_positions_m.dtype != np.float64:
            msg = (
                "ArraySpec.element_positions_m must be dtype float64 "
                f"(got {self.element_positions_m.dtype})."
            )
            raise ValueError(msg)
        if self.element_positions_m.shape != (self.n_elements, _TWO):
            msg = (
                "ArraySpec.element_positions_m must have shape "
                f"({self.n_elements}, 2); got {self.element_positions_m.shape}."
            )
            raise ValueError(msg)
        # Freeze the array against accidental mutation by downstream code.
        self.element_positions_m.flags.writeable = False

        if self.geometry is ArrayGeometry.ULA:
            self._validate_ula()
        elif self.geometry is ArrayGeometry.UCA:
            self._validate_uca()
        # CUSTOM places no geometric constraint -- the user's positions are
        # authoritative.

    def _validate_ula(self) -> None:
        """ULA must lie along the y-axis (x == 0) and have uniform spacing."""
        xs = self.element_positions_m[:, 0]
        if np.any(np.abs(xs) > _POSITION_TOLERANCE_M):
            msg = (
                "ArraySpec(geometry=ULA): all elements must lie on the y-axis "
                "(x == 0). The array-local convention places the ULA "
                "perpendicular to boresight (+x). Use ArraySpec.ula() to "
                "construct a conforming ULA."
            )
            raise ValueError(msg)
        ys = self.element_positions_m[:, 1]
        spacings = np.diff(ys)
        if spacings.size > 0:
            ref = spacings[0]
            if not np.all(np.abs(spacings - ref) < _POSITION_TOLERANCE_M):
                msg = (
                    "ArraySpec(geometry=ULA): element spacings along y must be "
                    f"uniform (got first {ref}, deltas {spacings.tolist()})."
                )
                raise ValueError(msg)

    def _validate_uca(self) -> None:
        """UCA elements must lie on a circle of constant radius around the origin."""
        radii = np.sqrt(self.element_positions_m[:, 0] ** 2 + self.element_positions_m[:, 1] ** 2)
        ref = radii[0]
        if not np.all(np.abs(radii - ref) < _POSITION_TOLERANCE_M):
            msg = (
                "ArraySpec(geometry=UCA): all elements must lie on a single "
                f"circle around the origin (radii {radii.tolist()})."
            )
            raise ValueError(msg)

    # ------------------------------------------------------------------
    # Convenience factories
    # ------------------------------------------------------------------

    @classmethod
    def ula(cls, n_elements: int, spacing_m: float) -> ArraySpec:
        """Construct a uniform linear array along the y-axis, channel 0 at origin.

        Element i at position ``(0, i * spacing_m)``. Boresight is +x.
        """
        if n_elements < _TWO:
            msg = f"ArraySpec.ula: n_elements must be >= 2 (got {n_elements})."
            raise ValueError(msg)
        if spacing_m <= 0.0:
            msg = f"ArraySpec.ula: spacing_m must be > 0 (got {spacing_m})."
            raise ValueError(msg)
        positions = np.zeros((n_elements, _TWO), dtype=np.float64)
        positions[:, 1] = np.arange(n_elements, dtype=np.float64) * spacing_m
        return cls(
            geometry=ArrayGeometry.ULA,
            n_elements=n_elements,
            element_positions_m=positions,
        )

    @classmethod
    def uca(cls, n_elements: int, radius_m: float) -> ArraySpec:
        """Construct a uniform circular array centred at the origin.

        Element i at ring angle ``alpha_i = 2*pi*i/n_elements``, position
        ``(radius_m * cos(alpha_i), radius_m * sin(alpha_i))``. Channel 0 at
        ``(radius_m, 0)``.
        """
        if n_elements < _TWO:
            msg = f"ArraySpec.uca: n_elements must be >= 2 (got {n_elements})."
            raise ValueError(msg)
        if radius_m <= 0.0:
            msg = f"ArraySpec.uca: radius_m must be > 0 (got {radius_m})."
            raise ValueError(msg)
        alphas = 2.0 * math.pi * np.arange(n_elements, dtype=np.float64) / n_elements
        positions = np.empty((n_elements, _TWO), dtype=np.float64)
        positions[:, 0] = radius_m * np.cos(alphas)
        positions[:, 1] = radius_m * np.sin(alphas)
        return cls(
            geometry=ArrayGeometry.UCA,
            n_elements=n_elements,
            element_positions_m=positions,
        )

    @classmethod
    def custom(cls, positions_xy_m: np.ndarray) -> ArraySpec:
        """Construct an arbitrary array from explicit (x, y) element positions.

        ``positions_xy_m`` must be shape ``(n_elements, 2)`` with dtype convertible
        to ``float64``. By convention place channel 0 at the origin, but no
        constraint is enforced.
        """
        positions = np.asarray(positions_xy_m, dtype=np.float64)
        if positions.ndim != _TWO or positions.shape[1] != _TWO:
            msg = (
                "ArraySpec.custom: positions_xy_m must be shape (n_elements, 2); "
                f"got {positions.shape}."
            )
            raise ValueError(msg)
        # Copy to ensure we own the buffer (and can freeze it in __post_init__).
        positions = np.ascontiguousarray(positions)
        return cls(
            geometry=ArrayGeometry.CUSTOM,
            n_elements=positions.shape[0],
            element_positions_m=positions,
        )

    # ------------------------------------------------------------------
    # Steering-vector computation
    # ------------------------------------------------------------------

    def steering_phases(self, delta_local_rad: float, wavelength_m: float) -> np.ndarray:
        """Per-channel relative phase (radians) for a far-field source.

        Returns a ``(n_elements,)`` ``float64`` array. Element i's phase is
        ``-2*pi * dot(p_i, k_hat) / lambda`` where ``k_hat = (cos delta, sin delta)``
        is the source-direction unit vector in the array-local frame and ``p_i``
        is the element position. The negative sign is the standard receive-array
        convention for an incoming plane wave.
        """
        if wavelength_m <= 0.0:
            msg = f"ArraySpec.steering_phases: wavelength_m must be > 0 (got {wavelength_m})."
            raise ValueError(msg)
        k_hat = np.array(
            [math.cos(delta_local_rad), math.sin(delta_local_rad)],
            dtype=np.float64,
        )
        path_delta = self.element_positions_m @ k_hat  # shape (n_elements,)
        phases: np.ndarray = -2.0 * math.pi * path_delta / wavelength_m
        return phases
