"""MGRS grid string for the detail panel — display only.

The `mgrs` package wraps a small C library and may lack a wheel on some
platforms. It is a UI convenience, never load-bearing, so the import is
optional: if it fails, `to_mgrs` returns None and the UI shows lat/lon only.
"""

from __future__ import annotations

try:  # pragma: no cover - import-time environment dependent
    import mgrs as _mgrs

    _CONVERTER: object | None = _mgrs.MGRS()
except Exception:  # noqa: BLE001 - any failure means "no MGRS available"
    _CONVERTER = None


def to_mgrs(lat_deg: float, lon_deg: float) -> str | None:
    """Return an MGRS grid string, or None if conversion is unavailable."""
    if _CONVERTER is None:
        return None
    try:
        return _CONVERTER.toMGRS(lat_deg, lon_deg)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
