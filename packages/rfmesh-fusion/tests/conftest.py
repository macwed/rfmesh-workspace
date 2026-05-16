"""Shared pytest fixtures for the rfmesh-fusion tests.

Kept deliberately small. Every fusion test sooner or later needs to
construct ``GeodeticPosition`` values; centralising the factory and a
canonical ENU origin here means later tickets (Stansfield, MLE, GDOP,
residuals, ellipse, fuser) inherit the same idioms without repeating
themselves.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    GeodeticPosition,
)

# Type alias for the ``make_position`` fixture's return type. ``Callable[..., T]``
# accepts any signature, which is what we want: tests call
# ``make_position(52.0, 21.0)`` positionally and sometimes pass
# ``hae_m=..`` / ``sigma_m=..`` by name; the runtime closure handles both.
MakePosition = Callable[..., GeodeticPosition]


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
