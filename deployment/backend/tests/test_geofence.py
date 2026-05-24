"""Tests for the outline→Jx-geofence path (geofence.py + the app route).

Two layers:
* pure helper (`outline_to_geofence_markers` / `geofence_remarks`) — geometry
  handling, labels/uids, and the honesty-locked remarks (cue-not-target, no dBm,
  never "safe");
* the `POST /fixes/{id}/geofence` route — fix lookup (404), geometry validation
  (422), the Jx counter, and CoT failure mapping (502) — driven by calling the
  coroutine directly with fakes so no DEM/seed/lifespan or TAK socket is needed.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest
from both3_poc.app import app, send_geofence
from both3_poc.geofence import geofence_remarks, outline_to_geofence_markers
from fastapi import HTTPException
from rfmesh_cot import CotError, operator_marker_to_cot_xml

_POLY = {
    "type": "Polygon",
    "coordinates": [[[4.0, 50.0], [4.1, 50.0], [4.1, 50.1], [4.0, 50.0]]],
}
_MULTI = {
    "type": "MultiPolygon",
    "coordinates": [
        [[[4.0, 50.0], [4.1, 50.0], [4.1, 50.1], [4.0, 50.0]]],
        [[[5.0, 51.0], [5.1, 51.0], [5.1, 51.1], [5.0, 51.0]]],
    ],
}


# ----- pure helper -------------------------------------------------------------


def test_polygon_makes_one_geofence_marker() -> None:
    markers = outline_to_geofence_markers(
        _POLY, label="J1", coverage_label="≥6% of peak plausibility", uid_seed="abc123"
    )
    assert len(markers) == 1
    m = markers[0]
    assert m.template_key == "geofence"
    assert m.callsign == "J1"
    assert m.uid == "jx-abc123"
    assert m.geofence is not None
    # GeoJSON [lon,lat] flipped to (lat,lon); closing dup dropped (encoder re-closes)
    assert m.vertices == ((50.0, 4.0), (50.0, 4.1), (50.1, 4.1))


def test_multipolygon_makes_one_marker_per_part() -> None:
    markers = outline_to_geofence_markers(
        _MULTI, label="J2", coverage_label="≥10% of peak plausibility", uid_seed="seed"
    )
    assert [m.uid for m in markers] == ["jx-seed-0", "jx-seed-1"]
    assert all(m.callsign == "J2" for m in markers)


def test_remarks_are_honesty_locked() -> None:
    r = geofence_remarks(
        "≥6% of peak plausibility", {"dem_source": "copernicus-30m", "burnthrough": True}
    )
    low = r.lower()
    assert "cue" in low and "not a confirmed location" in low  # not-a-target caveat
    assert "no dbm" in low  # power disclaimer
    assert "safe" not in low  # never an over-claim (ADR-016/017)
    assert "6% of peak" in r  # the chosen threshold is shown
    assert "copernicus-30m" in r  # descriptive context folded in
    assert "burnthrough" in low  # high-ERP caveat surfaced


@pytest.mark.parametrize(
    "geom",
    [
        {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]},  # < 3 distinct verts
        {"type": "Polygon", "coordinates": []},
        "not-a-dict",
    ],
)
def test_bad_geometry_raises(geom: object) -> None:
    with pytest.raises(ValueError):
        outline_to_geofence_markers(geom, label="J1", coverage_label="≥6% of peak", uid_seed="x")  # type: ignore[arg-type]


def test_marker_encodes_geofence_detail() -> None:
    m = outline_to_geofence_markers(_POLY, label="J1", coverage_label="≥6% of peak", uid_seed="enc")[0]
    ev = ET.fromstring(operator_marker_to_cot_xml(m))
    assert ev.attrib["type"] == "u-d-f"
    gf = ev.find("./detail/__geofence")
    assert gf is not None and gf.attrib["trigger"] == "Entry"
    remarks = ev.find("./detail/remarks")
    assert remarks is not None and "no dBm" in (remarks.text or "")


# ----- route -------------------------------------------------------------------


class _FakeStore:
    def __init__(self, known: object) -> None:
        self._known = known

    def get_fix(self, fix_id: object) -> object | None:
        return object() if fix_id == self._known else None


class _FakeSender:
    endpoint_url = "tcp://fake:8087"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.markers: list[object] = []

    async def send_marker(self, marker: object) -> None:
        if self.fail:
            raise CotError("boom")
        self.markers.append(marker)


def _route(fix_id: object, payload: object) -> dict:
    return asyncio.run(send_geofence(fix_id, payload))  # type: ignore[arg-type]


def test_route_unknown_fix_404() -> None:
    fid = uuid4()
    app.state.store = _FakeStore(known=uuid4())  # different id
    app.state.sender = _FakeSender()
    app.state.geofence_seq = 0
    with pytest.raises(HTTPException) as ei:
        _route(fid, {"geometry": _POLY})
    assert ei.value.status_code == 404


def test_route_missing_geometry_422() -> None:
    fid = uuid4()
    app.state.store = _FakeStore(known=fid)
    app.state.sender = _FakeSender()
    app.state.geofence_seq = 0
    with pytest.raises(HTTPException) as ei:
        _route(fid, {"p_band": 0.95})
    assert ei.value.status_code == 422


def test_route_assigns_incrementing_jx_labels() -> None:
    fid = uuid4()
    app.state.store = _FakeStore(known=fid)
    sender = _FakeSender()
    app.state.sender = sender
    app.state.geofence_seq = 0
    first = _route(fid, {"geometry": _POLY, "p_band": 0.95})
    second = _route(fid, {"geometry": _POLY, "p_band": 0.95})
    assert first["label"] == "J1" and first["sent"] == 1
    assert second["label"] == "J2"
    assert len(sender.markers) == 2


def test_route_cot_failure_502() -> None:
    fid = uuid4()
    app.state.store = _FakeStore(known=fid)
    app.state.sender = _FakeSender(fail=True)
    app.state.geofence_seq = 0
    with pytest.raises(HTTPException) as ei:
        _route(fid, {"geometry": _POLY})
    assert ei.value.status_code == 502
