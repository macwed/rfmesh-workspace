"""Tests for operator-authored CoT markers (``operator.py``).

Pure-encode tests -- no network. Cover:

* point marker renders the template's CoT type, uid, point, remarks;
* polygon marker renders a closed vertex ring + drawing styling;
* the polygon anchor point defaults to the vertex centroid;
* unknown template key fails loud (B3);
* geometry/template mismatch fails loud (B3);
* the delete event names the target uid with the TAK delete type.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest
from rfmesh_cot import (
    OperatorMarker,
    build_self_sa_xml,
    operator_delete_to_cot_xml,
    operator_marker_to_cot_xml,
    template,
)
from rfmesh_cot.exceptions import CotEncodingError


def _parse(blob: bytes) -> ET.Element:
    return ET.fromstring(blob)


def test_point_marker_basic_fields() -> None:
    m = OperatorMarker(
        template_key="hostile",
        uid="rfmesh.op.hostile.jammer-a",
        lat_deg=50.066_0,
        lon_deg=4.866_0,
        callsign="Jammer A",
        remarks="ELRS uplink, operator-confirmed",
        t_unix_ns=1_700_000_000_000_000_000,
    )
    ev = _parse(operator_marker_to_cot_xml(m))
    assert ev.tag == "event"
    assert ev.attrib["type"] == "a-h-G"
    assert ev.attrib["uid"] == "rfmesh.op.hostile.jammer-a"
    assert ev.attrib["how"] == "h-g-i-g-o"
    point = ev.find("point")
    assert point is not None
    assert float(point.attrib["lat"]) == pytest.approx(50.066, abs=1e-6)
    assert float(point.attrib["lon"]) == pytest.approx(4.866, abs=1e-6)
    contact = ev.find("./detail/contact")
    assert contact is not None and contact.attrib["callsign"] == "Jammer A"
    remarks = ev.find("./detail/remarks")
    assert remarks is not None and remarks.text == "ELRS uplink, operator-confirmed"
    # operator intent persists across ATAK restarts
    assert ev.find("./detail/archive") is not None


def test_callsign_defaults_to_template_label() -> None:
    m = OperatorMarker(template_key="waypoint", uid="u1", lat_deg=1.0, lon_deg=2.0)
    ev = _parse(operator_marker_to_cot_xml(m))
    contact = ev.find("./detail/contact")
    assert contact is not None and contact.attrib["callsign"] == "Waypoint"
    assert ev.attrib["type"] == "b-m-p-w"


def test_polygon_marker_closed_ring_and_styling() -> None:
    verts = ((50.0, 4.0), (50.0, 4.1), (50.1, 4.1), (50.1, 4.0))
    m = OperatorMarker(
        template_key="no_go",
        uid="rfmesh.op.no_go.north",
        callsign="No-go N",
        vertices=verts,
    )
    ev = _parse(operator_marker_to_cot_xml(m))
    assert ev.attrib["type"] == "u-d-f"
    links = ev.findall("./detail/link")
    # ring is closed: N+1 links, last == first
    assert len(links) == len(verts) + 1
    assert links[0].attrib["point"] == links[-1].attrib["point"]
    # drawing styling present for an area template
    assert ev.find("./detail/strokeColor") is not None
    assert ev.find("./detail/fillColor") is not None


def test_polygon_anchor_defaults_to_centroid() -> None:
    verts = ((0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0))
    m = OperatorMarker(template_key="search_area", uid="u2", vertices=verts)
    ev = _parse(operator_marker_to_cot_xml(m))
    point = ev.find("point")
    assert point is not None
    assert float(point.attrib["lat"]) == pytest.approx(1.0, abs=1e-6)
    assert float(point.attrib["lon"]) == pytest.approx(1.0, abs=1e-6)


def test_unknown_template_raises() -> None:
    with pytest.raises(CotEncodingError, match="unknown operator template"):
        template("does-not-exist")


def test_point_template_with_vertices_raises() -> None:
    m = OperatorMarker(
        template_key="hostile", uid="u3", lat_deg=1.0, lon_deg=2.0, vertices=((1.0, 2.0),)
    )
    with pytest.raises(CotEncodingError, match="point template"):
        operator_marker_to_cot_xml(m)


def test_polygon_template_too_few_vertices_raises() -> None:
    m = OperatorMarker(template_key="no_go", uid="u4", vertices=((1.0, 2.0), (1.0, 3.0)))
    with pytest.raises(CotEncodingError, match="needs >= 3"):
        operator_marker_to_cot_xml(m)


def test_delete_event_targets_uid() -> None:
    ev = _parse(operator_delete_to_cot_xml("rfmesh.op.hostile.jammer-a"))
    assert ev.attrib["type"] == "t-x-d-d"
    link = ev.find("./detail/link")
    assert link is not None and link.attrib["uid"] == "rfmesh.op.hostile.jammer-a"
    assert ev.find("./detail/__forcedelete") is not None


def test_custom_cot_type_override() -> None:
    m = OperatorMarker(
        template_key="custom",
        uid="u-custom",
        lat_deg=50.0,
        lon_deg=4.0,
        callsign="Air track",
        cot_type_override="a-h-A",
    )
    ev = _parse(operator_marker_to_cot_xml(m))
    assert ev.attrib["type"] == "a-h-A"  # operator-supplied, not the template default


def test_custom_template_defaults_without_override() -> None:
    m = OperatorMarker(template_key="custom", uid="u-c2", lat_deg=1.0, lon_deg=2.0)
    ev = _parse(operator_marker_to_cot_xml(m))
    assert ev.attrib["type"] == "a-u-G"  # template default when no override


def test_self_sa_is_friendly_with_group() -> None:
    ev = _parse(build_self_sa_xml("console.self.x", "13-cj-console", team="Cyan"))
    assert ev.attrib["type"] == "a-f-G-U-C"
    assert ev.attrib["uid"] == "console.self.x"
    contact = ev.find("./detail/contact")
    assert contact is not None and contact.attrib["callsign"] == "13-cj-console"
    grp = ev.find("./detail/__group")
    assert grp is not None and grp.attrib["name"] == "Cyan"


def test_stale_override_applies() -> None:
    m = OperatorMarker(
        template_key="spi",
        uid="u5",
        lat_deg=1.0,
        lon_deg=2.0,
        t_unix_ns=1_700_000_000_000_000_000,
        stale_after_s=5.0,
    )
    ev = _parse(operator_marker_to_cot_xml(m))
    # start + 5 s; just assert start != stale and both present (format checked elsewhere)
    assert ev.attrib["start"] != ev.attrib["stale"]
