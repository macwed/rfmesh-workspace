"""Parametric antenna patterns for the simulator.

The v0.1 implementation is a single class: ``AntennaPattern``, a smooth
cosine-taper approximation of a directional (Yagi-like) main beam,
parameterised by HPBW. The pattern shape is intentionally simple
because the L1 sweep peak-find only needs the *location* of the maximum
to be correct -- the absolute fidelity of the pattern shape outside the
main lobe is a problem for ATK-10 measurement, not WS-A-001 (see
``WS-A-001`` Acceptance Criterion 1h and the design hint).

Formula (relative power gain, linear):

    G(delta) = max( cos^2( pi * |delta| / (2 * HPBW) ), back_lobe_floor )
    for |delta| < HPBW;
    G(delta) = back_lobe_floor otherwise.

The taper hits 0.5 (-3 dB) at |delta| = HPBW/2, so a 50 deg HPBW antenna
has its -3 dB points at +/-25 deg. The floor keeps the back lobe at a
small but finite gain -- a real Yagi never has literal-zero back lobe and
flooring avoids producing -inf-dB RSSI bins that would break peak-find.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

# Decibel conversion divisors. RF convention: power scales 10*log10,
# voltage/amplitude scales 20*log10. Named here to keep PLR2004 quiet and
# to make the unit of the field next to each constant obvious.
_DB_POWER_DIVISOR = 10.0


class AntennaPattern(BaseModel):
    """Parametric directional-antenna pattern (a Yagi-like main beam).

    Internal data-leaf, used by ``SimulationScenario``. Frozen + extra=forbid
    is the same discipline applied to the contract types -- a typo'd key in
    a hand-built scenario fails at construction, not three reads in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hpbw_deg: float = Field(
        gt=0.0,
        le=180.0,
        description=(
            "Full half-power beamwidth in degrees. The -3 dB points are at "
            "+/- HPBW/2 off boresight; the full main lobe (gain >= -3 dB) is "
            "HPBW wide. The ATK-10's nominal HPBW is 50 deg."
        ),
    )
    back_lobe_floor_db: float = Field(
        default=-30.0,
        le=0.0,
        description=(
            "Minimum gain relative to boresight, in dB. Floors the cosine "
            "taper outside the main lobe so off-axis samples retain a small "
            "but finite gain. Default -30 dB."
        ),
    )

    def gain_linear(self, delta_deg: float) -> float:
        """Return power gain (linear, relative to boresight) at offset delta_deg.

        ``delta_deg`` should be the signed angle off boresight; the pattern is
        symmetric so only ``|delta|`` matters. Values outside [-180, 180] are
        accepted but are effectively saturated (the floor kicks in well
        before the cos**2 argument grows ambiguous).
        """
        abs_delta = abs(delta_deg)
        if abs_delta >= self.hpbw_deg:
            taper = 0.0
        else:
            arg = math.pi * abs_delta / (2.0 * self.hpbw_deg)
            taper = math.cos(arg) ** 2
        # Pydantic Field-typed attributes are seen as Any by mypy; the
        # explicit float() makes the return type concrete.
        floor_linear: float = 10.0 ** (float(self.back_lobe_floor_db) / _DB_POWER_DIVISOR)
        return max(taper, floor_linear)
