"""Emitter specifications for the simulator.

An ``EmitterSpec`` is the position-and-waveform of one source in the
simulation. The position is *relative to the receiver* (azimuth + range)
rather than global geodesy: the L1 simulator does not need lat/lon math,
and the receiver-relative form is what the antenna-pattern lookup needs
anyway. A future ticket that adds platform motion or a multi-node sim
can introduce a separate geodetic emitter spec; the L1 antenna pattern
will still consume receiver-relative bearings via a coordinate transform
in the scenario.

WS-A-001 supports only continuous-wave (CW) emitters. LoRa-chirp, FHSS,
and pulsed emitters arrive in later tickets driven by L3 classifier
needs (see ``WS-A-001`` Out-of-scope list).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EmitterSpec(BaseModel):
    """One CW emitter, positioned relative to the receiver.

    Frozen + extra=forbid for the same reason every contract type is:
    typos and stale fields fail at construction. ``tx_power_db`` and the
    scenario's ``noise_floor_dbfs`` share an arbitrary reference scale --
    only their ratio (the SNR) is physically meaningful.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    azimuth_deg: float = Field(
        ge=0.0,
        lt=360.0,
        description=(
            "Bearing from the receiver to the emitter, degrees true, "
            "clockwise from north, [0, 360). Matches the contracts' "
            "azimuth convention (INTERFACES.md Section 0)."
        ),
    )
    range_m: float = Field(
        gt=0.0,
        description="Distance from the receiver to the emitter, metres.",
    )
    frequency_hz: float = Field(
        gt=0.0,
        description=(
            "Emitter carrier frequency, Hz. The CW tone appears at baseband "
            "at (frequency_hz - SimulationScenario.center_freq_hz)."
        ),
    )
    tx_power_db: float = Field(
        description=(
            "Transmitter power in dB on the same arbitrary reference scale "
            "as SimulationScenario.noise_floor_dbfs. SDRs are not power-"
            "calibrated; only the ratio matters."
        ),
    )
    phase_deg: float = Field(
        default=0.0,
        ge=0.0,
        lt=360.0,
        description=(
            "Initial phase of the CW tone at the simulator's sample-zero, "
            "degrees, [0, 360). Deterministic -- carried by the scenario, "
            "not the RNG -- so reseeding noise does not perturb the signal."
        ),
    )
    modulation: Literal["cw"] = Field(
        default="cw",
        description=(
            "Waveform type. Only continuous-wave is supported in WS-A-001; "
            "LoRa/FHSS/pulsed arrive in later tickets."
        ),
    )
