"""Turn an RF-plausibility outline (GeoJSON) into ATAK geofence markers.

The locate.html operator picks one probability-mass band of the on-map
RF-plausibility posterior (the red emitter/jammer-likelihood outline) and pushes
it to ATAK as a monitored **geofence** labelled ``Jx`` (J1, J2 …). The frontend
sends the exact ring it is looking at (WYSIWYG); this module owns the parts that
must NOT be client-trusted: the honest remarks and the ``<__geofence>`` detail.

Honesty (ADR-016/017, AGENTS.md B3/B4): the posterior is a *cue, not a target*
and power is not modelled. The remarks always say so, and never claim "safe" or
quote dBm. The geometry itself is operator intent (``how="h-g-i-g-o"``), exactly
like every other operator marker.

Pure module — no I/O, no network. Unit-testable; the route in ``app.py`` wires it
to the store and the ``CotSender``. GeoJSON coordinate order is [lon, lat]; ATAK
markers want (lat, lon), so we flip here.
"""

from __future__ import annotations

import math
from typing import Any

from rfmesh_cot import GeofenceSpec, OperatorMarker

_MIN_RING_VERTICES = 3
_COORD_PAIR_LEN = 2  # a GeoJSON position is [lon, lat]
_M_PER_DEG_LAT = 111_320.0


def _bounding_sphere_m(ring: tuple[tuple[float, float], ...]) -> float:
    """Radius (m) from the ring centroid to its farthest vertex (equirect.).

    ATAK's ``GeoFence.fromCot`` reads ``boundingSphere``; supply a real value
    so the monitored sphere matches the drawn shape rather than defaulting.
    """
    clat = sum(p[0] for p in ring) / len(ring)
    clon = sum(p[1] for p in ring) / len(ring)
    m_lon = _M_PER_DEG_LAT * math.cos(math.radians(clat))
    far = 0.0
    for vlat, vlon in ring:
        dn = (vlat - clat) * _M_PER_DEG_LAT
        de = (vlon - clon) * m_lon
        far = max(far, math.hypot(dn, de))
    return round(far, 1)


def _ring_lonlat_to_latlon(ring: list[Any]) -> tuple[tuple[float, float], ...]:
    """Convert a GeoJSON [lon, lat] ring to ATAK ((lat, lon), …).

    Drops the trailing closing vertex if the ring repeats its first point —
    ``OperatorMarker`` re-closes the ring itself, so a kept duplicate would
    double the closing edge. Raises ``ValueError`` on a degenerate ring.
    """
    pts: list[tuple[float, float]] = []
    for pair in ring:
        if not isinstance(pair, (list, tuple)) or len(pair) < _COORD_PAIR_LEN:
            raise ValueError("ring vertex must be a [lon, lat] pair")
        lon, lat = float(pair[0]), float(pair[1])
        pts.append((lat, lon))
    if pts and pts[0] == pts[-1]:
        pts = pts[:-1]  # OperatorMarker closes the ring; drop the dup
    if len(pts) < _MIN_RING_VERTICES:
        raise ValueError(f"geofence ring needs >= {_MIN_RING_VERTICES} distinct vertices")
    return tuple(pts)


def _outer_rings(geometry: dict[str, Any]) -> list[tuple[tuple[float, float], ...]]:
    """Outer ring(s) of a GeoJSON Polygon or MultiPolygon, as (lat, lon) tuples.

    One ATAK geofence = one closed shape, so a MultiPolygon yields one ring per
    part. Holes (inner rings) are ignored — a geofence is its outer boundary.
    """
    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a GeoJSON object")
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Polygon":
        if not isinstance(coords, list) or not coords:
            raise ValueError("Polygon needs a non-empty coordinates array")
        return [_ring_lonlat_to_latlon(coords[0])]
    if gtype == "MultiPolygon":
        if not isinstance(coords, list) or not coords:
            raise ValueError("MultiPolygon needs a non-empty coordinates array")
        return [_ring_lonlat_to_latlon(poly[0]) for poly in coords if poly]
    raise ValueError(f"unsupported geometry type {gtype!r}; need Polygon or MultiPolygon")


def geofence_remarks(coverage_label: str, source_props: dict[str, Any] | None) -> str:
    """Server-owned, honesty-locked remarks for a geofence marker.

    Never client-trusted for the caveat. ``coverage_label`` is a short
    descriptor of the threshold the operator chose (e.g. "≥6% of peak
    plausibility"); the cue-not-target / no-dBm caveat is fixed text
    (ADR-016/017) and folds in terrain source + burnthrough when present.
    """
    props = source_props or {}
    parts = [
        f"RF-plausibility geofence — {coverage_label}.",
        "Cue, not a confirmed location: terrain-diffraction likelihood of the "
        "emitter ground. Confirm PID + a 2nd sensor before acting.",
        "Power not measured (no dBm).",
    ]
    dem = props.get("dem_source")
    if dem:
        parts.append(f"Terrain: {dem}.")
    if props.get("burnthrough"):
        parts.append("High-ERP jammer: terrain shadow unreliable (burnthrough likely).")
    return " ".join(parts)


def outline_to_geofence_markers(
    geometry: dict[str, Any],
    *,
    label: str,
    coverage_label: str,
    uid_seed: str,
    source_props: dict[str, Any] | None = None,
    monitor: str = "All",
    trigger: str = "Entry",
    stale_after_s: float | None = None,
) -> list[OperatorMarker]:
    """Build red ``geofence`` ``OperatorMarker``(s) from a GeoJSON outline.

    One marker per outer ring (a MultiPolygon → several). Each carries the Jx
    ``label`` as its callsign, a stable ``uid`` (``jx-<uid_seed>-<n>``), a
    :class:`GeofenceSpec` (so ATAK monitors entry/exit), and the honest
    remarks (``coverage_label`` describes the chosen threshold). Raises
    ``ValueError`` on geometry the encoder cannot represent — the route maps
    that to HTTP 422 (fail loud, B3).
    """
    rings = _outer_rings(geometry)
    remarks = geofence_remarks(coverage_label, source_props)
    markers: list[OperatorMarker] = []
    multi = len(rings) > 1
    for i, ring in enumerate(rings):
        uid = f"jx-{uid_seed}-{i}" if multi else f"jx-{uid_seed}"
        spec = GeofenceSpec(
            monitor=monitor, trigger=trigger, bounding_sphere_m=_bounding_sphere_m(ring)
        )
        markers.append(
            OperatorMarker(
                template_key="geofence",
                uid=uid,
                callsign=label,
                remarks=remarks,
                vertices=ring,
                stale_after_s=stale_after_s,
                geofence=spec,
            )
        )
    return markers
