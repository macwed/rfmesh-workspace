"""Shared geospatial value types.

Two small, frozen value objects used by both ``messages.py`` and ``config.py``:
a geodetic position (where a node is, where an emitter is) and a confidence
ellipse in a local tangent plane (how uncertain an emitter fix is). They live
in their own module because both the message layer and the config layer depend
on them, and nothing should depend on the message layer just to describe a
position.

COORDINATE CONVENTIONS (part of the frozen contract)
----------------------------------------------------
* Geodetic: WGS-84. Latitude/longitude in **decimal degrees**, height in
  **metres** above the WGS-84 ellipsoid (HAE), not above mean sea level. This
  matches what ``u-blox`` GNSS modules emit and what CoT / ATAK expect, so no
  datum conversion happens anywhere in the pipeline.
* Local tangent plane: **ENU** (East-North-Up), metres, right-handed. The
  fusion workstream picks one ENU origin per operation (typically the centroid
  of the deployed nodes) and does all least-squares geometry there; the origin
  travels with the ``FixEvent`` implicitly because the ellipse is only ever
  interpreted relative to the fix ``position``.
* Bearings/azimuths (defined on the message types, not here): degrees,
  **true north = 0, clockwise positive**. Never magnetic, never radians, on
  the wire. Conversions happen at the sensor edge.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeodeticPosition(BaseModel):
    """A point on the Earth in WGS-84, with an isotropic position uncertainty.

    Used for both node positions (from each node's GNSS fix) and emitter fix
    positions (from the fusion solver, projected back from ENU to geodetic).

    ``sigma_m`` is a single isotropic 1-sigma radius in metres -- deliberately
    scalar. Node GNSS uncertainty is well modelled as isotropic at the scale
    that matters here (a few metres). *Emitter* fixes are emphatically *not*
    isotropic -- their uncertainty is a stretched ellipse -- but that anisotropy
    is carried by ``FixEvent.confidence_ellipse_95``, not crammed in here. So
    on a ``FixEvent`` the ``position.sigma_m`` is a convenience scalar summary;
    the ellipse is the authoritative uncertainty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lat_deg: float = Field(
        ge=-90.0,
        le=90.0,
        description="WGS-84 latitude, decimal degrees, north positive.",
    )
    lon_deg: float = Field(
        ge=-180.0,
        le=180.0,
        description="WGS-84 longitude, decimal degrees, east positive.",
    )
    hae_m: float = Field(
        default=0.0,
        description=(
            "Height above the WGS-84 ellipsoid, metres. Default 0.0 for "
            "bench/sim use where altitude is irrelevant; real nodes populate "
            "it from GNSS so CoT/ATAK render at the correct elevation."
        ),
    )
    sigma_m: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Isotropic 1-sigma position uncertainty in metres. For a node, "
            "the GNSS fix uncertainty. For an emitter FixEvent, a scalar "
            "convenience summary only -- the authoritative, anisotropic "
            "uncertainty is FixEvent.confidence_ellipse_95."
        ),
    )


class EllipseENU(BaseModel):
    """A 2-D confidence ellipse in the local East-North tangent plane.

    This is how the fusion workstream expresses the anisotropic uncertainty of
    an emitter fix. A bearings-only cross-fix is almost never isotropic: with
    two sensors the error region is stretched along the bisector of the
    bearing lines; geometry (GDOP) stretches or compresses it further. An
    honest system shows that ellipse rather than a single "accuracy" number --
    this is also a credibility point with an RF/EW-expert jury (see
    ``docs/runbook-demo.md``).

    The ellipse is referenced to the ``FixEvent.position`` it accompanies: its
    centre *is* that position. ``orientation_deg`` is measured from the local
    East axis toward North (mathematical positive in the ENU plane).

    By convention this carries the **95% confidence** ellipse (hence the field
    name on ``FixEvent``). For a 2-D Gaussian that is the 2-sigma-ish contour:
    semi-axis = sqrt(chi2_inv(0.95, df=2)) * sigma_principal ~= 2.448 * sigma.
    The fusion workstream owns that conversion and documents it in
    ``INTERFACES.md``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    semi_major_m: float = Field(
        gt=0.0,
        description="Semi-major axis length, metres (the larger semi-axis).",
    )
    semi_minor_m: float = Field(
        gt=0.0,
        description="Semi-minor axis length, metres (the smaller semi-axis).",
    )
    orientation_deg: float = Field(
        ge=-180.0,
        le=180.0,
        description=(
            "Orientation of the semi-major axis, degrees, measured from local "
            "East toward North (ENU-plane mathematical positive)."
        ),
    )

    @model_validator(mode="after")
    def _major_ge_minor(self) -> EllipseENU:
        """Enforce the semantic invariant that 'major' is not smaller than 'minor'.

        Without this check a producer could swap the axes and every downstream
        consumer that assumes ``semi_major_m >= semi_minor_m`` (e.g. when
        picking a marker scale, or reporting "worst-case error") would be
        quietly wrong. Cheap invariant, caught at construction.
        """
        if self.semi_major_m < self.semi_minor_m:
            msg = (
                "EllipseENU.semi_major_m must be >= semi_minor_m "
                f"(got major={self.semi_major_m}, minor={self.semi_minor_m}); "
                "swap the axes and rotate orientation_deg by 90 deg."
            )
            raise ValueError(msg)
        return self

    @property
    def area_m2(self) -> float:
        """Area of the ellipse in square metres -- a single scalar 'how bad is it'.

        Handy for logging, for sorting candidate fixes, and for the demo
        dashboard's "ellipse shrinking as nodes are added" visual. Not part of
        the wire format; derived on demand.
        """
        return math.pi * self.semi_major_m * self.semi_minor_m
