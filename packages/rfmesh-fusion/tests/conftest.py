"""Shared pytest fixtures for the rfmesh-fusion tests.

Kept deliberately small. Every fusion test sooner or later needs to
construct ``GeodeticPosition`` values; centralising the factory and a
canonical ENU origin here means later tickets (Stansfield, MLE, GDOP,
residuals, ellipse, fuser) inherit the same idioms without repeating
themselves.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pytest
from rfmesh_contracts.enums import Capability  # type: ignore[import-untyped, unused-ignore]
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    GeodeticPosition,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)

# Type alias for the ``make_position`` fixture's return type. ``Callable[..., T]``
# accepts any signature, which is what we want: tests call
# ``make_position(52.0, 21.0)`` positionally and sometimes pass
# ``hae_m=..`` / ``sigma_m=..`` by name; the runtime closure handles both.
MakePosition = Callable[..., GeodeticPosition]
MakeBearing = Callable[..., BearingReport]

# A plausible UTC nanosecond timestamp -- 2026-05-14T00:00:00Z, integer
# ns since the Unix epoch. ``BearingReport.t_unix_ns`` requires ``> 0``;
# this constant satisfies the validator while not implying anything
# operational. Tests that care about ordering set their own values.
_DEFAULT_T_UNIX_NS: int = 1_778_976_000_000_000_000


@pytest.fixture
def make_position() -> MakePosition:
    """Return a factory that builds ``GeodeticPosition`` instances.

    Used as ``make_position(52.0, 21.0)`` or with explicit ``hae_m`` /
    ``sigma_m`` keywords. The factory exists because Pydantic models do
    not take positional arguments by default and writing
    ``GeodeticPosition(lat_deg=..., lon_deg=...)`` everywhere clutters
    the tests.
    """

    def _factory(
        lat_deg: float,
        lon_deg: float,
        *,
        hae_m: float = 0.0,
        sigma_m: float = 0.0,
    ) -> GeodeticPosition:
        return GeodeticPosition(
            lat_deg=lat_deg,
            lon_deg=lon_deg,
            hae_m=hae_m,
            sigma_m=sigma_m,
        )

    return _factory


@pytest.fixture
def origin(make_position: MakePosition) -> GeodeticPosition:
    """Canonical ENU origin: central Poland, near Warsaw.

    52.0 N, 21.0 E. Matches the simulator fixtures in
    ``packages/rfmesh-sdr/tests/conftest.py`` so cross-package scenarios
    later on share one geodetic anchor.
    """
    return make_position(52.0, 21.0)


@pytest.fixture
def make_bearing(make_position: MakePosition) -> MakeBearing:
    """Return a factory that builds ``BearingReport`` instances.

    Defaults cover every contract-validated field of
    ``BearingReport`` so a test that cares about geometry can write::

        make_bearing(azimuth_deg=90.0, sigma_deg=1.0, node_position=...)

    and not have to repeat ``node_id`` / ``t_unix_ns`` / ``method`` /
    etc. on every call. Every keyword on the underlying model is
    overridable; the factory passes them through verbatim.

    Defaults:

    * ``node_id`` -- ``"test-node"`` (min length 1).
    * ``t_unix_ns`` -- a fixed plausible 2026 timestamp.
    * ``node_position`` -- the canonical ENU origin (52 N, 21 E) at
      ``sigma_m = 5`` to match a smartphone GNSS survey.
    * ``azimuth_deg`` -- ``0.0`` (due north).
    * ``sigma_deg`` -- ``1.0`` deg (a representative L2 sigma).
    * ``method`` -- ``Capability.L2_MUSIC``.

    The factory takes ``sigma_deg`` as a positional-friendly alias for
    ``azimuth_sigma_deg`` because the latter is awkward at call sites.
    Optional fields (``snr_db``, ``emitter_class``,
    ``classification_confidence``, ``raw_pseudospectrum``) default to
    ``None`` as the contract specifies.
    """
    default_position = make_position(52.0, 21.0, sigma_m=5.0)

    def _factory(
        *,
        azimuth_deg: float = 0.0,
        sigma_deg: float = 1.0,
        node_position: GeodeticPosition | None = None,
        node_id: str = "test-node",
        t_unix_ns: int = _DEFAULT_T_UNIX_NS,
        method: Capability = Capability.L2_MUSIC,
        snr_db: float | None = None,
    ) -> BearingReport:
        return BearingReport(
            node_id=node_id,
            t_unix_ns=t_unix_ns,
            node_position=node_position if node_position is not None else default_position,
            azimuth_deg=azimuth_deg,
            azimuth_sigma_deg=sigma_deg,
            method=method,
            snr_db=snr_db,
        )

    return _factory


@pytest.fixture
def seeded_rng() -> np.random.Generator:
    """Return a numpy ``Generator`` with a fixed seed for determinism.

    Used by Monte-Carlo and noise-injecting tests so that re-running
    them yields byte-identical pass/fail outcomes. Tests that need
    independent draws within the same scenario should derive their
    own per-scenario seed -- this fixture is the single canonical
    "I need *some* deterministic RNG" entry point.
    """
    return np.random.default_rng(seed=20260517)


def azimuth_node_to_emitter_deg(
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
) -> float:
    """Return the exact azimuth from ``node_enu`` to ``emitter_enu``.

    Degrees, true north = 0, CW positive, wrapped to ``[0, 360)``.
    Mirrors ``geometry.bearing_to_unit_vector``'s ``(east, north) =
    (sin, cos)`` convention: ``azimuth = atan2(east_offset,
    north_offset)`` -- east first, north second.

    Lives in ``conftest.py`` so any test (Stansfield, MLE, future
    GDOP/ellipse Monte Carlo) shares one canonical "node-to-emitter
    azimuth" helper rather than re-deriving the convention each time
    and risking a sign flip.
    """
    de = emitter_enu[0] - node_enu[0]
    dn = emitter_enu[1] - node_enu[1]
    az_deg = math.degrees(math.atan2(de, dn))
    return az_deg % 360.0
