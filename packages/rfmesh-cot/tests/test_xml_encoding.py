"""Tests for ``fix_event_to_cot_xml`` -- CoT XML structure and byte-exactness.

Tests:

* ``test_xml_parses``                        -- output parses as XML.
* ``test_root_event_attributes``             -- root attrs are correct.
* ``test_event_type_matches_emitter_class``  -- CoT type reflects class.
* ``test_point_lat_lon_hae``                 -- point carries position.
* ``test_shape_ellipse_attrs``               -- <shape><ellipse> values.
* ``test_polygon_link_count``                -- >= 72 polygon <link>s.
* ``test_remarks_contents``                  -- remarks line has the
                                                 four required keys.
* ``test_stale_time_is_30s_after_t_unix_ns`` -- honesty for replays.
* ``test_byte_exact_against_canonical``      -- golden-file regression.
* ``test_classification_is_none_when_no_emitter_class``
                                              -- remarks degrade
                                                 gracefully.
* ``test_node_status_to_cot_xml_friendly_type`` -- node heartbeat
                                                    renders as friendly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

# Note: ``make_fix_event`` and ``deterministic_fix_event`` are fixtures
# from conftest.py (pytest auto-discovery). We type the factory as a
# Callable returning FixEvent rather than importing from conftest --
# AGENTS.md §3.5 forbids ``__init__.py`` under tests/, which would
# make ``tests.conftest`` an importable module.
from typing import Any
from uuid import UUID
from xml.etree import ElementTree as ET

import pytest
from rfmesh_contracts import (
    Capability,
    EmitterClass,
    FixEvent,
    GeodeticPosition,
    NodeStatus,
)
from rfmesh_cot import (
    STALE_AFTER_S,
    fix_event_to_cot_xml,
    node_status_to_cot_xml,
)

FixEventFactory = Any  # alias: callable producing FixEvent (see conftest)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_xml_parses(make_fix_event: FixEventFactory) -> None:
    """The output is well-formed XML."""
    fix = make_fix_event()
    blob = fix_event_to_cot_xml(fix)
    # Should not raise.
    root = ET.fromstring(blob)
    assert root.tag == "event"


def test_root_event_attributes(make_fix_event: FixEventFactory) -> None:
    """The <event> root has version=2.0, the right uid prefix, type, how."""
    fix = make_fix_event(
        fix_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        emitter_class=EmitterClass.UNKNOWN,
    )
    root = ET.fromstring(fix_event_to_cot_xml(fix, callsign_prefix="rfmesh"))
    assert root.attrib["version"] == "2.0"
    assert root.attrib["how"] == "m-r"
    assert root.attrib["type"] == "a-h-G-E-X-N"
    assert root.attrib["uid"] == "rfmesh.fix.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    # time/start/stale present.
    assert "time" in root.attrib
    assert "start" in root.attrib
    assert "stale" in root.attrib


@pytest.mark.parametrize(
    ("cls", "expected_type"),
    [
        (None, "a-h-G-E-X-N"),
        (EmitterClass.UNKNOWN, "a-h-G-E-X-N"),
        (EmitterClass.ELRS, "a-h-G-E-X-N-RC"),
        (EmitterClass.CROSSFIRE, "a-h-G-E-X-N-RC"),
        (EmitterClass.DRONEID, "a-h-G-E-X-N-RC"),
        (EmitterClass.GSM_JAMMER, "a-h-G-E-X-N-J"),
        (EmitterClass.POLE21, "a-h-G-E-X-N-J"),
        (EmitterClass.VOLNOREZ, "a-h-G-E-X-N-J"),
    ],
)
def test_event_type_matches_emitter_class(
    make_fix_event: FixEventFactory,
    cls: EmitterClass | None,
    expected_type: str,
) -> None:
    """Every EmitterClass member maps to its expected CoT type in the XML."""
    fix = make_fix_event(emitter_class=cls)
    root = ET.fromstring(fix_event_to_cot_xml(fix))
    assert root.attrib["type"] == expected_type


def test_point_lat_lon_hae(make_fix_event: FixEventFactory) -> None:
    """<point> carries the fix position; ce = ellipse semi-major."""
    fix = make_fix_event(
        lat_deg=12.345678,
        lon_deg=-98.765432,
        hae_m=42.5,
        semi_major_m=125.0,
    )
    root = ET.fromstring(fix_event_to_cot_xml(fix))
    point = root.find("point")
    assert point is not None
    assert float(point.attrib["lat"]) == pytest.approx(12.345678, abs=1e-7)
    assert float(point.attrib["lon"]) == pytest.approx(-98.765432, abs=1e-7)
    assert float(point.attrib["hae"]) == pytest.approx(42.5, abs=1e-3)
    # ce is the ellipse semi-major (conservative isotropic upper bound).
    assert float(point.attrib["ce"]) == pytest.approx(125.0, abs=1e-3)
    assert point.attrib["le"] == "9999999.0"


def test_shape_ellipse_attrs(make_fix_event: FixEventFactory) -> None:
    """<detail><shape><ellipse> has major/minor/angle in metres/degrees."""
    fix = make_fix_event(
        semi_major_m=120.5,
        semi_minor_m=40.25,
        orientation_deg=-67.5,
    )
    root = ET.fromstring(fix_event_to_cot_xml(fix))
    ellipse = root.find("detail/shape/ellipse")
    assert ellipse is not None
    assert float(ellipse.attrib["major"]) == pytest.approx(120.5, abs=1e-3)
    assert float(ellipse.attrib["minor"]) == pytest.approx(40.25, abs=1e-3)
    assert float(ellipse.attrib["angle"]) == pytest.approx(-67.5, abs=1e-3)


def test_polygon_link_count(make_fix_event: FixEventFactory) -> None:
    """At least 72 <link> polygon vertices (the default plus closure).

    The default n_vertices=72 yields a 73-vertex closed polygon. The
    XML must carry all of them as <link> children of <detail>.
    """
    fix = make_fix_event()
    root = ET.fromstring(fix_event_to_cot_xml(fix))
    links = root.findall("detail/link")
    assert len(links) >= 72
    # All <link>s carry a "point" attribute of the form "lat,lon".
    for link in links:
        pt = link.attrib["point"]
        lat_str, lon_str = pt.split(",")
        # Just confirm they parse as floats; values are already
        # checked by test_polygon_vertices.py.
        _ = float(lat_str)
        _ = float(lon_str)


def test_remarks_contents(make_fix_event: FixEventFactory) -> None:
    """<remarks> contains method=, GDOP=, range_m=, classification= keys."""
    fix = make_fix_event(
        method="stansfield+mle",
        gdop=4.5,
        emitter_class=EmitterClass.CROSSFIRE,
    )
    root = ET.fromstring(fix_event_to_cot_xml(fix))
    remarks_elem = root.find("detail/remarks")
    assert remarks_elem is not None
    assert remarks_elem.text is not None
    text = remarks_elem.text
    assert "method=stansfield+mle" in text
    assert "GDOP=4." in text  # truncated by metre precision
    assert "range_m=" in text
    assert "classification=crossfire" in text


def test_classification_is_none_when_no_emitter_class(
    make_fix_event: FixEventFactory,
) -> None:
    """When ``emitter_class is None``, remarks says ``classification=none``."""
    fix = make_fix_event(emitter_class=None)
    root = ET.fromstring(fix_event_to_cot_xml(fix))
    remarks_elem = root.find("detail/remarks")
    assert remarks_elem is not None
    assert remarks_elem.text is not None
    assert "classification=none" in remarks_elem.text


def test_stale_time_is_30s_after_t_unix_ns(make_fix_event: FixEventFactory) -> None:
    """The stale time is t_unix_ns + STALE_AFTER_S, not now+STALE_AFTER_S.

    Replay honesty: a fix recorded last week, replayed today, must
    have stale time relative to its own timestamp.
    """
    fix = make_fix_event(t_unix_ns=1_600_000_000_000_000_000)  # 2020-09-13
    root = ET.fromstring(fix_event_to_cot_xml(fix))
    time_str = root.attrib["time"]
    stale_str = root.attrib["stale"]
    # Parse both as UTC datetimes; difference should be STALE_AFTER_S.
    t0 = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    t1 = datetime.strptime(stale_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    delta = (t1 - t0).total_seconds()
    assert delta == pytest.approx(STALE_AFTER_S, abs=1e-2)
    # And the times themselves are *from 2020*, not from today.
    assert t0.year == 2020
    assert t0.month == 9


def test_byte_exact_against_canonical(
    deterministic_fix_event: FixEvent,
) -> None:
    """Byte-exact regression against tests/fixtures/canonical_fix.xml.

    A change to the CoT XML layout (attribute order, precision,
    indentation, element nesting) must be deliberate -- if this test
    fails, regenerate the fixture with documented rationale.
    """
    blob = fix_event_to_cot_xml(deterministic_fix_event)
    canonical = (_FIXTURES_DIR / "canonical_fix.xml").read_bytes()
    assert blob == canonical, (
        "CoT XML drift: regenerate canonical_fix.xml deliberately if this change is intentional."
    )


def test_node_status_to_cot_xml_friendly_type() -> None:
    """``node_status_to_cot_xml`` produces a friendly (a-f-...) marker."""
    status = NodeStatus(
        node_id="node-rtl-01",
        t_unix_ns=1_700_000_000_000_000_000,
        position=GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=3.0),
        active_capabilities=(Capability.L1_RSSI,),
        gnss_locked=False,
        healthy=True,
        status_detail="",
    )
    blob = node_status_to_cot_xml(status)
    root = ET.fromstring(blob)
    assert root.attrib["type"].startswith("a-f-")  # friendly, not hostile
    assert root.attrib["uid"] == "rfmesh.node.node-rtl-01"
    remarks_elem = root.find("detail/remarks")
    assert remarks_elem is not None
    assert remarks_elem.text is not None
    assert "gnss_locked=false" in remarks_elem.text
    assert "healthy=true" in remarks_elem.text


def test_lat_lon_precision_is_7_decimal_places(make_fix_event: FixEventFactory) -> None:
    """lat/lon attributes have exactly 7 decimal digits (~1.1 cm)."""
    fix = make_fix_event(lat_deg=50.0, lon_deg=5.0)
    root = ET.fromstring(fix_event_to_cot_xml(fix))
    point = root.find("point")
    assert point is not None
    lat_str = point.attrib["lat"]
    lon_str = point.attrib["lon"]
    # Format check: 'X.XXXXXXX' or '-X.XXXXXXX'
    assert lat_str.count(".") == 1
    assert len(lat_str.split(".")[1]) == 7
    assert lon_str.count(".") == 1
    assert len(lon_str.split(".")[1]) == 7
