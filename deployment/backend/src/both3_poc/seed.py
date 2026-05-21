"""Parsing FixEvents from JSON, plus loading the demo seed file.

Two input shapes are supported on ingest and seeding:

1. **Nested** — a real `FixEvent` dump (what rfmesh-fusion produces via
   `model_dump(mode="json")`): has `position`, `covariance_m2`,
   `confidence_ellipse_95`, etc. Validated straight through the contract model.

2. **Flat** — the demo artifact shape in
   `docs/demo/artifacts/trench_demo_fixes.json`: top-level `lat_deg`,
   `semi_major_m`, `semi_minor_m`, `orientation_deg`, ... and crucially
   **no `covariance_m2`**. We synthesize the covariance from the 95% ellipse.

Covariance synthesis (the inverse of fusion's `cov_to_ellipse`):
the 95% ellipse semi-axes are `k * sigma_principal` with
`k = sqrt(chi2_inv(0.95, df=2)) ~= 2.44774683` (INTERFACES.md §2 / EllipseENU
docstring). So divide each semi-axis by k to recover principal sigmas, then
rotate `diag(sa^2, sb^2)` by `orientation_deg` (semi-major measured from East
toward North) into ENU to get (sigma_xx, sigma_xy, sigma_yy).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from rfmesh_contracts import BearingReport, EllipseENU, FixEvent, GeodeticPosition

# sqrt(chi2_inv(0.95, df=2)); fixed constant, no scipy needed.
_CHI2_95_DF2_SQRT = 2.4477468306808161


def _covariance_from_ellipse(
    semi_major_m: float, semi_minor_m: float, orientation_deg: float
) -> tuple[float, float, float]:
    """Synthesize (sigma_xx, sigma_xy, sigma_yy) ENU covariance from a 95% ellipse."""
    sa = semi_major_m / _CHI2_95_DF2_SQRT
    sb = semi_minor_m / _CHI2_95_DF2_SQRT
    theta = math.radians(orientation_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    va = sa * sa
    vb = sb * sb
    # C = va * u u^T + vb * v v^T, u=(cos,sin) along major, v=(-sin,cos)
    xx = va * cos_t * cos_t + vb * sin_t * sin_t
    yy = va * sin_t * sin_t + vb * cos_t * cos_t
    xy = (va - vb) * sin_t * cos_t
    return (xx, xy, yy)


def _flat_to_fix_event(d: dict[str, Any]) -> FixEvent:
    """Map a flat demo record into a valid FixEvent."""
    semi_major = float(d["semi_major_m"])
    semi_minor = float(d["semi_minor_m"])
    orientation = float(d["orientation_deg"])

    contributing = tuple(d.get("contributing_nodes", ()))
    residuals = tuple(float(r) for r in d.get("residuals_deg", ()))
    if len(residuals) != len(contributing):
        msg = (
            "flat fix: len(residuals_deg) != len(contributing_nodes) "
            f"({len(residuals)} vs {len(contributing)}) for fix {d.get('fix_id')!r}"
        )
        raise ValueError(msg)

    position = GeodeticPosition(
        lat_deg=float(d["lat_deg"]),
        lon_deg=float(d["lon_deg"]),
        hae_m=float(d.get("hae_m", 0.0)),
        sigma_m=float(d.get("sigma_m", 0.0)),
    )
    ellipse = EllipseENU(
        semi_major_m=semi_major,
        semi_minor_m=semi_minor,
        orientation_deg=orientation,
    )
    # Build kwargs without schema_version so the contract default (the imported
    # SCHEMA_VERSION) applies — never hard-code it, or the Literal pin rejects it.
    return FixEvent(
        fix_id=d["fix_id"],
        t_unix_ns=int(d["t_unix_ns"]),
        position=position,
        covariance_m2=_covariance_from_ellipse(semi_major, semi_minor, orientation),
        confidence_ellipse_95=ellipse,
        confidence_level=d["confidence_level"],
        contributing_nodes=contributing,
        residuals_deg=residuals,
        gdop=float(d["gdop"]),
        method=str(d["method"]),
        emitter_class=d.get("emitter_class"),
    )


def parse_fix(d: dict[str, Any]) -> FixEvent:
    """Parse one fix dict, accepting either the nested or the flat shape."""
    if "position" in d or "confidence_ellipse_95" in d:
        return FixEvent.model_validate(d)
    return _flat_to_fix_event(d)


def load_seed_fixes(path: Path) -> list[FixEvent]:
    """Load and parse the demo fixes seed file. Returns [] if the file is missing."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw if isinstance(raw, list) else [raw]
    return [parse_fix(r) for r in records]


def load_seed_bearings(path: Path) -> list[BearingReport]:
    """Load and parse the demo bearings seed file. Returns [] if the file is missing."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw if isinstance(raw, list) else [raw]
    return [BearingReport.model_validate(r) for r in records]
