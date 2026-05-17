"""``FixEvent`` -> CoT XML rendering and ``EmitterClass`` -> CoT type mapping.

Pure functions, no I/O, no asyncio. Composes ``ellipse_to_polygon_vertices``
and the closed ``EmitterClass`` -> CoT-type-designator dispatch.

CoT (Cursor-on-Target) is the TAK ecosystem's wire format: a small XML
document with an ``<event>`` root, a ``<point>`` (lat/lon/hae/ce/le), and
a ``<detail>`` block that may carry shape, link, remarks, etc. The
specification we target is the version ATAK 4.x and FreeTAKServer 1.x
consume; PyTAK >= 6 ships against the same.

CoT TYPE STRINGS (atom-affiliation-dimension-category-...)
----------------------------------------------------------
Every CoT type we emit starts with ``a-h-G-E-X-N`` -- atom, hostile,
Ground (dimension), Equipment (category), eXternal (subcategory), and
the broad slot 'N' for nonspecific electronic. The next character
refines:

* ``a-h-G-E-X-N``     -- unclassified electronic emitter (UNKNOWN / None)
* ``a-h-G-E-X-N-RC``  -- remote-control link (ELRS / CROSSFIRE / DRONEID)
* ``a-h-G-E-X-N-J``   -- jammer (GSM_JAMMER / POLE21 / VOLNOREZ)

The mapping is closed and never raises (Invariant B3 -- a future
``EmitterClass`` member that no one has updated this table for should
degrade gracefully to "nonspecific electronic", not crash the publisher
mid-demo). Adding a class to the contract WITHOUT updating this table
is allowed; it just means new classes land in the nonspecific bin until
the next ``rfmesh-cot`` release.

STALE TIME
----------
CoT events carry ``start`` (when the event was observed) and ``stale``
(when ATAK should stop trusting it). We set ``stale = fix.t_unix_ns +
STALE_AFTER_S`` -- relative to the fix's own timestamp, NOT to the
encoder's wall clock. This is the honesty requirement for replay: a
recorded fix played back next week is still meaningful in its own
30-second window, and ATAK can show "this fix was from 2026-05-15T...
and went stale 30 s later".

OUTPUT FORMAT
-------------
We emit bytes (UTF-8 encoded XML) so the publisher can hand them
straight to PyTAK's ``writer.write(blob)`` -- PyTAK's wire layer is
byte-oriented. The bytes start with a single ``<event>`` root, no XML
declaration prefix (CoT-over-TCP frames events that way; TAK clients
expect to read one event per TCP write).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

from rfmesh_contracts import EmitterClass, FixEvent, NodeStatus

from .ellipse import ellipse_to_polygon_vertices

#: CoT stale time, seconds. The fix is "fresh" from t_unix_ns to
#: t_unix_ns + STALE_AFTER_S. After that ATAK should fade or drop the
#: marker. 30 s is the FreeTAKServer/ATAK default for transient hostile
#: contacts; tuneable on a per-deployment basis but fixed here for
#: simplicity.
STALE_AFTER_S: float = 30.0

#: How precisely lat/lon are rendered. 7 decimal degrees ~= 1.1 cm at
#: the equator, well below any rfmesh fix sigma; rendering more would
#: just be misleading precision.
_LATLON_PRECISION: int = 7

#: How precisely hae and metre-quantities are rendered. 3 decimals
#: = 1 mm, again well below any sensor sigma.
_METRE_PRECISION: int = 3


def emitter_class_to_cot_type(cls: EmitterClass | None) -> str:
    """Map an ``EmitterClass`` (or ``None``) to a CoT type string.

    See module docstring for the table. The mapping is closed and never
    raises -- unknown classes degrade to ``a-h-G-E-X-N`` (nonspecific
    electronic). Returning a *valid* CoT type for every input is the
    contract; the demo must never abort because the classifier produced
    a label we hadn't anticipated.
    """
    if cls is None or cls is EmitterClass.UNKNOWN:
        return "a-h-G-E-X-N"
    if cls in (EmitterClass.ELRS, EmitterClass.CROSSFIRE, EmitterClass.DRONEID):
        return "a-h-G-E-X-N-RC"
    if cls in (EmitterClass.GSM_JAMMER, EmitterClass.POLE21, EmitterClass.VOLNOREZ):
        return "a-h-G-E-X-N-J"
    # Belt-and-braces -- a future member added to the enum without a
    # case here lands in the nonspecific bin rather than raising.
    return "a-h-G-E-X-N"  # pragma: no cover -- exhaustive above


def _format_cot_time(t_unix_ns: int) -> str:
    """Format a Unix-ns timestamp as the W3C-XML datetime CoT expects.

    Example: ``2026-05-17T12:34:56.789Z``. Always UTC, always with a
    trailing 'Z'. Microsecond-precision (CoT clients ignore anything
    finer; ns would be over-precise wire noise).
    """
    seconds = t_unix_ns / 1e9
    dt = datetime.fromtimestamp(seconds, tz=UTC)
    # isoformat() gives '+00:00'; CoT uses 'Z'. Trim microseconds to ms
    # for compactness (TAK clients accept either).
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _format_stale_time(t_unix_ns: int, stale_after_s: float = STALE_AFTER_S) -> str:
    """Format the stale time = ``t_unix_ns + stale_after_s``."""
    base = datetime.fromtimestamp(t_unix_ns / 1e9, tz=UTC)
    stale = base + timedelta(seconds=stale_after_s)
    return stale.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stale.microsecond // 1000:03d}Z"


def _range_m_text(
    fix_lat_deg: float,
    fix_lon_deg: float,
    contributing_node_count: int,
) -> str:
    """Operator-facing range string for CoT remarks.

    Returns ``"n/a"`` in v1.0 because the FixEvent does not carry the
    operator's view-center position (the node positions live in the
    bearings, not in the fix). Writing ``"0.000"`` looked like a real
    zero-metre measurement on the ATAK marker; ``"n/a"`` matches what
    ``rfmesh-ops.FixPanel`` shows when its operator-view origin is
    unknown (ADR-009 follow-up; demo-integrity council R1).

    A future ticket may take an optional ``observer_position`` argument
    and compute a real range; for v1.0 the percentage display lives in
    ``rfmesh-ops`` where it has access to the full geometry.
    """
    _ = (fix_lat_deg, fix_lon_deg, contributing_node_count)
    return "n/a"


def _build_uid(fix: FixEvent, callsign_prefix: str) -> str:
    """Construct a stable-ish CoT UID for this fix.

    We key on ``fix_id`` (a contract UUID) so each fix gets a unique
    ATAK marker. The architect doc §6.3 parking-lot item notes that a
    *stable track UID* (one ATAK track for a moving emitter, many fixes)
    is a future refinement -- for v1.0 each fix is its own marker, which
    is the most honest mapping (the operator sees each new solution
    explicitly).
    """
    return f"{callsign_prefix}.fix.{fix.fix_id}"


def fix_event_to_cot_xml(
    fix: FixEvent,
    callsign_prefix: str = "rfmesh",
) -> bytes:
    """Render a ``FixEvent`` as a CoT XML ``<event>`` blob.

    The returned bytes are a single ``<event>`` element (no XML
    declaration, no document wrapper). Layout:

    .. code-block:: xml

        <event version="2.0" type="a-h-G-E-X-N" uid="rfmesh.fix.<uuid>"
               time="2026-05-17T12:34:56.789Z"
               start="2026-05-17T12:34:56.789Z"
               stale="2026-05-17T12:35:26.789Z"
               how="m-r">
          <point lat="50.123" lon="5.678" hae="0.0"
                 ce="<semi_major_m>" le="9999999.0"/>
          <detail>
            <contact callsign="rfmesh"/>
            <shape>
              <ellipse major="<semi_major_m>" minor="<semi_minor_m>"
                       angle="<orientation_deg>"/>
            </shape>
            <link point="lat,lon" .../>   <!-- N+1 polygon vertices -->
            <link point="..."/>
            ...
            <remarks>method=stansfield+mle GDOP=3.45 range_m=n/a
                     classification=elrs confidence=high
                     contributing_nodes=node-a,node-b,node-c</remarks>
          </detail>
        </event>

    The ``ce`` attribute on ``<point>`` is the CoT-standard "circular
    error" 1-sigma; we set it to the ellipse semi-major (the
    *worst-case* axis), so any TAK client that only renders a
    circular accuracy still sees an honest *upper bound* of our
    uncertainty rather than an under-statement.
    """
    cot_type = emitter_class_to_cot_type(fix.emitter_class)

    time_str = _format_cot_time(fix.t_unix_ns)
    stale_str = _format_stale_time(fix.t_unix_ns, STALE_AFTER_S)

    event = ET.Element(
        "event",
        attrib={
            "version": "2.0",
            "type": cot_type,
            "uid": _build_uid(fix, callsign_prefix),
            "time": time_str,
            "start": time_str,
            "stale": stale_str,
            # 'm-r' = "machine-derived, radio direction-finding"
            "how": "m-r",
        },
    )

    # The <point>. CoT 'ce' is a 1-sigma circular error in metres; we
    # report the ellipse semi-major as a conservative upper bound on
    # the isotropic uncertainty. 'le' is linear (vertical) error -- we
    # have no vertical info, CoT convention is 9999999.0 for "unknown".
    semi_major = fix.confidence_ellipse_95.semi_major_m
    ET.SubElement(
        event,
        "point",
        attrib={
            "lat": f"{fix.position.lat_deg:.{_LATLON_PRECISION}f}",
            "lon": f"{fix.position.lon_deg:.{_LATLON_PRECISION}f}",
            "hae": f"{fix.position.hae_m:.{_METRE_PRECISION}f}",
            "ce": f"{semi_major:.{_METRE_PRECISION}f}",
            "le": "9999999.0",
        },
    )

    detail = ET.SubElement(event, "detail")

    # <contact callsign=...> so the operator can tell who emitted the
    # fix. Standard TAK convention.
    ET.SubElement(detail, "contact", attrib={"callsign": callsign_prefix})

    # <shape><ellipse .../></shape> -- the primary uncertainty
    # representation. ATAK 4.x renders this natively.
    shape = ET.SubElement(detail, "shape")
    ET.SubElement(
        shape,
        "ellipse",
        attrib={
            "major": f"{fix.confidence_ellipse_95.semi_major_m:.{_METRE_PRECISION}f}",
            "minor": f"{fix.confidence_ellipse_95.semi_minor_m:.{_METRE_PRECISION}f}",
            "angle": f"{fix.confidence_ellipse_95.orientation_deg:.{_METRE_PRECISION}f}",
        },
    )

    # <link point="lat,lon"/> per polygon vertex. ATAK clients that
    # do not parse <shape><ellipse> render this as a polygon outline.
    # The polygon is closed by construction (last vertex == first).
    polygon = ellipse_to_polygon_vertices(
        fix.confidence_ellipse_95,
        fix.position.lat_deg,
        fix.position.lon_deg,
    )
    for lat, lon in polygon:
        ET.SubElement(
            detail,
            "link",
            attrib={
                "point": (f"{lat:.{_LATLON_PRECISION}f},{lon:.{_LATLON_PRECISION}f}"),
                "relation": "p-p",  # 'point-to-point' in CoT link taxonomy
            },
        )

    # <remarks> -- the human-readable diagnostics line. The four facts
    # the architect doc §2.1 binds:
    #   method=...  (which solver mode)
    #   GDOP=...    (geometry quality)
    #   range_m=... ("n/a" until observer-position context is wired; R1)
    #   classification=... (the EmitterClass label, or 'none')
    classification_str = fix.emitter_class.value if fix.emitter_class is not None else "none"
    contributing = ",".join(fix.contributing_nodes)
    range_m_text = _range_m_text(
        fix.position.lat_deg,
        fix.position.lon_deg,
        len(fix.contributing_nodes),
    )
    remarks_text = (
        f"method={fix.method} "
        f"GDOP={fix.gdop:.{_METRE_PRECISION}f} "
        f"range_m={range_m_text} "
        f"classification={classification_str} "
        f"confidence={fix.confidence_level.value} "
        f"contributing_nodes={contributing}"
    )
    remarks = ET.SubElement(detail, "remarks")
    remarks.text = remarks_text

    blob: bytes = ET.tostring(event, encoding="utf-8")
    return blob


def node_status_to_cot_xml(
    status: NodeStatus,
    callsign_prefix: str = "rfmesh",
) -> bytes:
    """Render a ``NodeStatus`` heartbeat as a CoT XML ``<event>`` blob.

    Friendly (not hostile) marker -- it represents one of *our* nodes,
    not a target. CoT type ``a-f-G-U-C`` (atom-friendly-Ground-Unit-
    Combat) is the closest TAK type for an unmanned ground sensor.
    The ``healthy`` flag and the ``gnss_locked`` flag both surface in
    remarks so the operator can see GNSS denial at a glance.

    Not strictly required by the architect §2.1 spec but included so
    ``PyTAKCotPublisher.publish_node_status`` has something to ship.
    """
    time_str = _format_cot_time(status.t_unix_ns)
    stale_str = _format_stale_time(status.t_unix_ns, STALE_AFTER_S)
    uid = f"{callsign_prefix}.node.{status.node_id}"

    event = ET.Element(
        "event",
        attrib={
            "version": "2.0",
            "type": "a-f-G-U-C",
            "uid": uid,
            "time": time_str,
            "start": time_str,
            "stale": stale_str,
            "how": "m-g",
        },
    )
    ET.SubElement(
        event,
        "point",
        attrib={
            "lat": f"{status.position.lat_deg:.{_LATLON_PRECISION}f}",
            "lon": f"{status.position.lon_deg:.{_LATLON_PRECISION}f}",
            "hae": f"{status.position.hae_m:.{_METRE_PRECISION}f}",
            "ce": f"{status.position.sigma_m:.{_METRE_PRECISION}f}"
            if status.position.sigma_m > 0.0
            else "9999999.0",
            "le": "9999999.0",
        },
    )
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", attrib={"callsign": status.node_id})
    caps_str = ",".join(c.value for c in status.active_capabilities)
    remarks_text = (
        f"node_id={status.node_id} "
        f"healthy={'true' if status.healthy else 'false'} "
        f"gnss_locked={'true' if status.gnss_locked else 'false'} "
        f"active_capabilities={caps_str} "
        f"status_detail={status.status_detail or 'ok'}"
    )
    remarks = ET.SubElement(detail, "remarks")
    remarks.text = remarks_text
    blob: bytes = ET.tostring(event, encoding="utf-8")
    return blob
