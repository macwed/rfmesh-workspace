"""Shared test helpers for the rfmesh-fusion tests.

Holds the non-fixture exports that several test modules import directly
(type aliases for the factory fixtures + the azimuth-convention helper).
Kept in a sibling module rather than ``conftest.py`` so the conftest
remains a pure pytest-discovery file (fixtures + hooks) and shared
*types/functions* live where standard Python imports can reach them
without making ``tests/`` a package.

The matching ``conftest.py`` adds the tests directory to ``sys.path`` so
test modules can ``from _helpers import ...`` directly — this mirrors the
``import golden_generator`` pattern in ``packages/rfmesh-dsp/tests/``
(ADR-006: no ``__init__.py`` under ``tests/``).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from rfmesh_contracts.geospatial import GeodeticPosition
from rfmesh_contracts.messages import BearingReport

# Type alias for the ``make_position`` fixture's return type. ``Callable[..., T]``
# accepts any signature, which is what we want: tests call
# ``make_position(52.0, 21.0)`` positionally and sometimes pass
# ``hae_m=..`` / ``sigma_m=..`` by name; the runtime closure handles both.
MakePosition = Callable[..., GeodeticPosition]
MakeBearing = Callable[..., BearingReport]


def azimuth_node_to_emitter_deg(
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
) -> float:
    """Return the exact azimuth from ``node_enu`` to ``emitter_enu``.

    Degrees, true north = 0, CW positive, wrapped to ``[0, 360)``.
    Mirrors ``geometry.bearing_to_unit_vector``'s ``(east, north) =
    (sin, cos)`` convention: ``azimuth = atan2(east_offset,
    north_offset)`` -- east first, north second.

    Lives in ``_helpers.py`` so any test (Stansfield, MLE, future
    GDOP/ellipse Monte Carlo) shares one canonical "node-to-emitter
    azimuth" helper rather than re-deriving the convention each time
    and risking a sign flip.
    """
    de = emitter_enu[0] - node_enu[0]
    dn = emitter_enu[1] - node_enu[1]
    az_deg = math.degrees(math.atan2(de, dn))
    return az_deg % 360.0
