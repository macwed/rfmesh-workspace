"""Top-level immutable description of one simulation scenario.

A ``SimulationScenario`` is everything ``SyntheticReceiver`` needs to know
that does *not* change per ``read(n)``: the emitters, the antenna pattern,
the channel model, the noise floor, the sample rate, the centre frequency.
Per-``read`` state (heading, sample-offset counter, RNG state, opened flag)
lives on ``SyntheticReceiver``.

A frozen ``@dataclass`` rather than a Pydantic model because the
``channel`` field is typed as the ``ChannelModel`` Protocol -- Pydantic's
runtime validator does not have a clean story for Protocol-typed fields,
and the scenario is internal data so Pydantic's JSON/YAML round-tripping
buys us nothing here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .antenna import AntennaPattern
from .array import ArraySpec
from .channel import ChannelModel, FreeSpaceChannel
from .emitter import EmitterSpec
from .impairments import ChannelImpairments


def _default_channel() -> ChannelModel:
    """Construct the default ``FreeSpaceChannel`` for the dataclass factory."""
    return FreeSpaceChannel()


@dataclass(frozen=True)
class SimulationScenario:
    """Frozen scenario passed to a SyntheticReceiver at construction.

    Validation lives in ``__post_init__``: at least one emitter, sample rate
    and centre frequency positive, noise floor finite. Pydantic-style
    discipline applied at the dataclass layer.

    Coherent-mode fields (WS-A-002):

    * ``array`` -- when present, the receiver enters L2 coherent mode, exposes
      ``read_coherent`` / ``calibrate`` / ``is_calibrated``, and is recognised
      as a ``CoherentReceiver`` by structural typing. When ``None``, the
      scenario is single-channel L1 (WS-A-001 behaviour).
    * ``impairments`` -- optional per-channel complex offsets injected between
      the antenna and the ADC. Must agree with ``array.n_elements`` when set.
    * ``calibration_reference_snr_db`` -- the SNR of the simulated noise-source
      reference used during ``calibrate()``. Default 30 dB; reducing it below
      the per-channel ADC noise floor causes calibration to fail.
    """

    emitters: tuple[EmitterSpec, ...]
    antenna: AntennaPattern
    sample_rate_hz: float
    center_freq_hz: float
    noise_floor_dbfs: float = -100.0
    channel: ChannelModel = field(default_factory=_default_channel)
    array: ArraySpec | None = None
    impairments: ChannelImpairments | None = None
    calibration_reference_snr_db: float = 30.0

    def __post_init__(self) -> None:
        """Enforce the scenario-level invariants the receiver depends on.

        These are the same kind of cross-field checks that the contracts'
        Pydantic ``@model_validator`` enforces -- moved into ``__post_init__``
        because the dataclass form does not run Pydantic validation.
        """
        if not self.emitters:
            msg = "SimulationScenario requires at least one emitter."
            raise ValueError(msg)
        if self.sample_rate_hz <= 0.0:
            msg = f"SimulationScenario.sample_rate_hz must be > 0 (got {self.sample_rate_hz})."
            raise ValueError(msg)
        if self.center_freq_hz <= 0.0:
            msg = f"SimulationScenario.center_freq_hz must be > 0 (got {self.center_freq_hz})."
            raise ValueError(msg)
        if not math.isfinite(self.noise_floor_dbfs):
            msg = (
                f"SimulationScenario.noise_floor_dbfs must be finite (got {self.noise_floor_dbfs})."
            )
            raise ValueError(msg)
        if not math.isfinite(self.calibration_reference_snr_db):
            msg = (
                "SimulationScenario.calibration_reference_snr_db must be finite "
                f"(got {self.calibration_reference_snr_db})."
            )
            raise ValueError(msg)
        if self.impairments is not None:
            if self.array is None:
                msg = (
                    "SimulationScenario carries per-channel impairments but no "
                    "array; impairments are only meaningful in coherent mode."
                )
                raise ValueError(msg)
            if self.impairments.n_elements != self.array.n_elements:
                msg = (
                    "SimulationScenario.impairments has "
                    f"{self.impairments.n_elements} channels but array has "
                    f"{self.array.n_elements}; they must match."
                )
                raise ValueError(msg)
