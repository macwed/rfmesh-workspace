"""Operator-authored CoT markers -- templates, geometry, pure encode.

The existing ``markers.py`` is *machine* output: the fusion server turns a
``FixEvent`` into a hostile-emitter marker. This module is the other
direction -- the *operator* drops a marker (a contact, a waypoint, a
no-go area) by choosing a **template** and a **point or polygon**, and
this module renders it to the same CoT wire format every ATAK / WinTAK /
iTAK client already speaks.

WHY THIS LIVES IN ``rfmesh-cot`` AND NOT IN ``rfmesh-contracts``
---------------------------------------------------------------
``OperatorMarker`` and ``MessageTemplate`` never cross a workstream
boundary -- they flow ops -> cot, both inside Workstream C+D. The frozen
``CotPublisher`` Protocol (``rfmesh-contracts``) is untouched; the
concrete ``PyTAKCotPublisher`` simply grows ``publish_marker`` /
``delete_marker`` methods. So no contract change, no ``SCHEMA_VERSION``
bump, no B1 event. See ``docs/adr/ADR-018-operator-authored-cot-messaging.md``.

TEMPLATES
---------
A template is the operator's vocabulary: "hostile contact", "no-go
area", "waypoint". Each fixes the CoT *type* string (the 2525 affiliation
or the drawing-shape designator), the geometry kind (point vs polygon),
the default stale time, and any drawing styling. The operator picks a
template by key, supplies geometry + a callsign + optional free text,
and gets a valid CoT ``<event>`` blob.

The registry is *closed and never raises on encode* in the same spirit
as ``emitter_class_to_cot_type`` -- but template *lookup* DOES raise
``CotEncodingError`` for an unknown key, because an operator asking to
send a template that does not exist is a bug to surface, not a marker to
fabricate (Invariant B3 -- fail loud).

STABLE UID = EDITABLE MARKER
----------------------------
Every operator marker carries a *stable* ``uid``. Re-sending the same
uid with a new position / time is how ATAK *moves* a marker; sending a
``t-x-d-d`` delete event for that uid is how it is *removed*. This is
why the ``OperatorMarkerStore`` keys on uid -- one operator intent, one
uid, many CoT events over its lifetime.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Literal

from .exceptions import CotEncodingError
from .markers import (
    _LATLON_PRECISION,
    _METRE_PRECISION,
    _format_cot_time,
    _format_stale_time,
)

GeometryKind = Literal["point", "polygon"]

#: CoT ``how`` for an operator-placed marker: human-entered via a GPS /
#: map input ("garbage in, garbage out" lineage). This is the value ATAK
#: writes for a manually dropped marker, and it tells any consumer that
#: the position is *operator intent*, not a machine-derived fix ("m-r").
_HOW_HUMAN_INPUT: str = "h-g-i-g-o"

#: CoT ``how`` for the delete sentinel.
_HOW_DELETE: str = "h-g-i-g-o"

#: "Unknown / not applicable" sentinel CoT uses for circular & linear
#: error when there is no meaningful accuracy figure (operator marker has
#: no covariance -- it is exactly where the operator pointed).
_CE_UNKNOWN: str = "9999999.0"

#: Minimum vertices for a closed polygon area (a triangle).
_MIN_POLYGON_VERTICES: int = 3


@dataclass(frozen=True)
class MessageTemplate:
    """One entry in the operator's message vocabulary.

    Parameters
    ----------
    key:
        Stable lookup key (e.g. ``"hostile"``). Appears in CLI / UI and
        in the marker uid; renaming one is an operator-visible change.
    label:
        Human-facing name shown in the picker and used as the default
        ``<contact callsign>`` if the operator gives none.
    cot_type:
        The CoT type designator. For point contacts this is a 2525
        affiliation (``a-h-G`` hostile, ``a-f-G`` friendly, ...); for
        areas it is a TAK drawing-shape type (``u-d-f`` free-form).
    geometry:
        ``"point"`` (one lat/lon) or ``"polygon"`` (>= 3 vertices).
    default_stale_s:
        How long the marker stays "fresh" in ATAK before it fades, in
        seconds. Operator markers outlive transient fixes -- a no-go
        area is not a 30 s contact -- so defaults are generous and
        per-template.
    stroke_argb / fill_argb:
        Optional ATAK drawing colours for polygons, as signed 32-bit
        ARGB integers (ATAK's wire convention). ``None`` for point
        templates.
    """

    key: str
    label: str
    cot_type: str
    geometry: GeometryKind
    default_stale_s: float
    stroke_argb: int | None = None
    fill_argb: int | None = None


# ARGB colours as ATAK writes them (signed 32-bit). Alpha in the high
# byte. Fills are ~25% alpha so the underlying map stays readable.
_RED = -65536  # 0xFFFF0000
_RED_FILL = 0x40FF0000 - (1 << 32)  # 25% red
_AMBER = -256  # 0xFFFFFF00
_AMBER_FILL = 0x40FFFF00 - (1 << 32)
_BLUE = -16737844  # 0xFF00A0CC-ish cyan-blue
_BLUE_FILL = 0x4000A0CC - (1 << 32)

#: The operator's message catalogue. Keep small and tactical -- this is
#: the BoTH3 demo vocabulary, not a full 2525 set. Adding a template is a
#: one-line change here; consumers (CLI, future map UI) read the registry
#: and need no per-template code.
TEMPLATES: dict[str, MessageTemplate] = {
    # --- point contacts (2525 affiliation frames) ---------------------
    "hostile": MessageTemplate("hostile", "Hostile contact", "a-h-G", "point", 3600.0),
    "friendly": MessageTemplate("friendly", "Friendly", "a-f-G", "point", 3600.0),
    "neutral": MessageTemplate("neutral", "Neutral", "a-n-G", "point", 3600.0),
    "unknown": MessageTemplate("unknown", "Unknown contact", "a-u-G", "point", 3600.0),
    # --- point tactical graphics --------------------------------------
    "waypoint": MessageTemplate("waypoint", "Waypoint", "b-m-p-w", "point", 86400.0),
    "spi": MessageTemplate("spi", "Sensor point of interest", "b-m-p-s-p-i", "point", 600.0),
    "casevac": MessageTemplate("casevac", "CASEVAC", "b-r-f-h-c", "point", 86400.0),
    # --- operator-drawn areas (TAK free-form drawing shape) -----------
    "no_go": MessageTemplate("no_go", "No-go area", "u-d-f", "polygon", 86400.0, _RED, _RED_FILL),
    "area_of_interest": MessageTemplate(
        "area_of_interest", "Area of interest", "u-d-f", "polygon", 86400.0, _AMBER, _AMBER_FILL
    ),
    "search_area": MessageTemplate(
        "search_area", "Search area", "u-d-f", "polygon", 86400.0, _BLUE, _BLUE_FILL
    ),
}


def template(key: str) -> MessageTemplate:
    """Look up a template by key or raise ``CotEncodingError``.

    Lookup raises (does not degrade) because an operator selecting a
    template that does not exist is a bug to surface, not a marker to
    silently invent (B3).
    """
    try:
        return TEMPLATES[key]
    except KeyError:
        known = ", ".join(sorted(TEMPLATES))
        msg = f"unknown operator template {key!r}; known templates: {known}"
        raise CotEncodingError(msg) from None


@dataclass(frozen=True)
class OperatorMarker:
    """One operator-authored marker: a template applied to geometry.

    Parameters
    ----------
    template_key:
        Key into :data:`TEMPLATES`.
    uid:
        Stable unique id. Re-sending the same uid moves the marker;
        deleting it removes it. The CLI / store generate one if the
        operator gives none.
    lat_deg / lon_deg:
        For a point template, the marker position. For a polygon, the
        anchor point ATAK shows -- defaults to the vertex centroid when
        left as ``None`` (see ``__post_init__``-style resolution in
        :func:`operator_marker_to_cot_xml`).
    callsign:
        Label shown on the map. Defaults to the template label.
    remarks:
        Free text -- the operator's note. Shown when the marker is
        tapped in ATAK. This is the "geochat-lite" annotation path.
    vertices:
        Polygon vertices as ``((lat, lon), ...)`` for area templates;
        ``None`` for point templates. Must be ``None`` for point
        templates and length >= 3 for polygon templates (validated at
        encode time).
    hae_m:
        Height above the WGS-84 ellipsoid, metres. Default 0.0.
    stale_after_s:
        Override the template's default stale time. ``None`` uses the
        template default.
    t_unix_ns:
        Event time, integer ns since epoch. ``None`` means "stamp at
        encode with the wall clock" -- operator markers are live, not
        replays, so unlike ``FixEvent`` there is no honesty constraint
        tying this to a recorded instant.
    """

    template_key: str
    uid: str
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    callsign: str = ""
    remarks: str = ""
    vertices: tuple[tuple[float, float], ...] | None = None
    hae_m: float = 0.0
    stale_after_s: float | None = None
    t_unix_ns: int | None = None
    # Internal: extra <detail> children are out of scope for v1; field
    # kept so future templates (chat recipients, links) extend without a
    # signature change.
    _extra: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _resolve_point(marker: OperatorMarker, tmpl: MessageTemplate) -> tuple[float, float]:
    """Return the (lat, lon) for the marker's ``<point>`` element.

    Point templates use the marker's own lat/lon. Polygon templates
    anchor at the vertex centroid unless the operator set an explicit
    lat/lon (non-zero), so the area's label sits in its middle.
    """
    if tmpl.geometry == "point":
        return marker.lat_deg, marker.lon_deg
    verts = marker.vertices or ()
    if marker.lat_deg or marker.lon_deg:
        return marker.lat_deg, marker.lon_deg
    n = len(verts)
    lat = sum(v[0] for v in verts) / n
    lon = sum(v[1] for v in verts) / n
    return lat, lon


def _validate(marker: OperatorMarker, tmpl: MessageTemplate) -> None:
    """Fail loud (B3) on geometry that does not match the template."""
    if tmpl.geometry == "point" and marker.vertices is not None:
        msg = f"template {tmpl.key!r} is a point template; vertices must be None"
        raise CotEncodingError(msg)
    if tmpl.geometry == "polygon":
        verts = marker.vertices or ()
        if len(verts) < _MIN_POLYGON_VERTICES:
            msg = (
                f"template {tmpl.key!r} is a polygon template; needs >= "
                f"{_MIN_POLYGON_VERTICES} vertices, got {len(verts)}"
            )
            raise CotEncodingError(msg)


def operator_marker_to_cot_xml(marker: OperatorMarker) -> bytes:
    """Render an :class:`OperatorMarker` as a CoT ``<event>`` blob.

    Returns a single UTF-8 ``<event>`` element (no XML declaration),
    ready to hand to ``PyTAKCotPublisher`` exactly like
    ``fix_event_to_cot_xml`` output.
    """
    tmpl = template(marker.template_key)
    _validate(marker, tmpl)

    t_ns = marker.t_unix_ns if marker.t_unix_ns is not None else time.time_ns()
    stale_s = marker.stale_after_s if marker.stale_after_s is not None else tmpl.default_stale_s
    callsign = marker.callsign or tmpl.label
    lat, lon = _resolve_point(marker, tmpl)

    time_str = _format_cot_time(t_ns)
    stale_str = _format_stale_time(t_ns, stale_s)

    event = ET.Element(
        "event",
        attrib={
            "version": "2.0",
            "type": tmpl.cot_type,
            "uid": marker.uid,
            "time": time_str,
            "start": time_str,
            "stale": stale_str,
            "how": _HOW_HUMAN_INPUT,
        },
    )
    ET.SubElement(
        event,
        "point",
        attrib={
            "lat": f"{lat:.{_LATLON_PRECISION}f}",
            "lon": f"{lon:.{_LATLON_PRECISION}f}",
            "hae": f"{marker.hae_m:.{_METRE_PRECISION}f}",
            "ce": _CE_UNKNOWN,
            "le": _CE_UNKNOWN,
        },
    )
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", attrib={"callsign": callsign})

    if tmpl.geometry == "polygon":
        _append_polygon(detail, marker, tmpl)

    if marker.remarks:
        remarks = ET.SubElement(detail, "remarks")
        remarks.text = marker.remarks

    # <archive/> tells ATAK to persist the marker across app restarts --
    # operator intent should survive, unlike a transient fix.
    ET.SubElement(detail, "archive")

    for tag, text in marker._extra:
        child = ET.SubElement(detail, tag)
        child.text = text

    blob: bytes = ET.tostring(event, encoding="utf-8")
    return blob


def _append_polygon(
    detail: ET.Element,
    marker: OperatorMarker,
    tmpl: MessageTemplate,
) -> None:
    """Append the closed vertex ring + drawing styling to ``<detail>``.

    ATAK renders a ``u-d-f`` free-form shape from ``<link point=>``
    vertices; the ring is closed by repeating the first vertex last so
    every client draws a sealed polygon.
    """
    verts = list(marker.vertices or ())
    ring = [*verts, verts[0]]  # close it
    for vlat, vlon in ring:
        ET.SubElement(
            detail,
            "link",
            attrib={
                "point": f"{vlat:.{_LATLON_PRECISION}f},{vlon:.{_LATLON_PRECISION}f}",
                "relation": "c",  # 'contains' -- TAK shape-vertex relation
            },
        )
    if tmpl.stroke_argb is not None:
        ET.SubElement(detail, "strokeColor", attrib={"value": str(tmpl.stroke_argb)})
        ET.SubElement(detail, "strokeWeight", attrib={"value": "3.0"})
    if tmpl.fill_argb is not None:
        ET.SubElement(detail, "fillColor", attrib={"value": str(tmpl.fill_argb)})
    # Tell ATAK the shape is a closed area, label it, keep it on screen.
    ET.SubElement(detail, "labels_on", attrib={"value": "true"})


def operator_delete_to_cot_xml(uid: str, t_unix_ns: int | None = None) -> bytes:
    """Render a CoT *delete* event that removes the marker with ``uid``.

    Uses the TAK ``t-x-d-d`` ("tasking - delete") convention: a tiny
    event whose ``<detail><link uid=.../>`` names the marker to drop on
    every connected client. This is how an operator un-sends a marker
    cleanly rather than waiting for it to go stale.
    """
    t_ns = t_unix_ns if t_unix_ns is not None else time.time_ns()
    time_str = _format_cot_time(t_ns)
    # A delete is instantaneous; a short stale window is conventional.
    stale_str = _format_stale_time(t_ns, 10.0)
    event = ET.Element(
        "event",
        attrib={
            "version": "2.0",
            "type": "t-x-d-d",
            "uid": f"{uid}.delete",
            "time": time_str,
            "start": time_str,
            "stale": stale_str,
            "how": _HOW_DELETE,
        },
    )
    # A point is required by the CoT schema even for a delete; 0,0 is the
    # TAK convention for "not meaningful".
    ET.SubElement(
        event,
        "point",
        attrib={"lat": "0.0", "lon": "0.0", "hae": "0.0", "ce": _CE_UNKNOWN, "le": _CE_UNKNOWN},
    )
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "link", attrib={"uid": uid, "relation": "none", "type": "none"})
    ET.SubElement(detail, "__forcedelete")
    blob: bytes = ET.tostring(event, encoding="utf-8")
    return blob
