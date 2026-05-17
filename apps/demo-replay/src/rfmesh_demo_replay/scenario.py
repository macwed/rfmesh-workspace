"""``Scenario`` -- app-internal Pydantic models + YAML loader for demo-replay.

A ``Scenario`` is everything the ``ReplayOrchestrator`` needs to bring up
a multi-node demo run from a single YAML file. It is *not* a contract
(``rfmesh_contracts`` is frozen; see ``ARCHITECTURE.md`` §3); the
schema here evolves with the demo and does not gate a ``SCHEMA_VERSION``
bump.

DESIGN POSTURE
--------------
* App-internal types. Live in ``rfmesh_demo_replay.scenario``; not
  re-exported from any package boundary; not on any wire.
* Pydantic with ``extra="forbid"`` + frozen, mirroring the contracts
  discipline -- a typo'd key in ``scenarios/trench_demo.yaml`` is a
  loud parse-time error, not a silent miss (Invariant 4 surface).
* Composition root for the demo: the loader produces ENU coords,
  emitter spec, channel description, node specs (carrying their
  matching ``NodeConfig`` via ``to_node_config``), beat sequencing,
  and a ``FusionConfig`` -- a complete graph the orchestrator can
  spawn from.

YAML SHAPE (mirrors ``scenarios/trench_demo.yaml``)
---------------------------------------------------
::

    scenario_id: trench_demo_v1
    description: ...
    enu_origin: {lat_deg, lon_deg, hae_m}
    emitter:
        emitter_id: target_01
        enu_position_m: [east, north]
        center_freq_hz: 915e6
        modulation: cw
        emit_power_db_above_noise: 25.0
        expected_class: UNKNOWN
    channel:
        kind: free_space | two_ray_ground | multipath_fir | log_normal_shadowing | composite
        ... params depending on kind
    nodes:
        - node_id: node-l1-west
          enu_position_m: [east, north]
          sigma_m: 5.0
          heading_deg: 90.0
          capabilities: [l1_rssi]
          sdr:
              driver: sim
              sample_rate_hz: 2.0e6
              center_freq_hz: 915.0e6
              gain_db: 30.0
              bias_tee: false
          array: ...        # optional, required for L2 nodes
          bearer:
              kind: wifi
              heartbeat_interval_s: 2.0
          expected_sigma_deg: 5.0
    demo_beats:
        - beat_id: A
          label: ...
          duration_s: 10
          active_nodes: [node-l1-south]
          expected_fix: null | { ... }
          dashboard_caption: ...
    fusion:
        listen_url: udp://0.0.0.0:9000
        batch_window_ms: 100.0
        ...
    expected_outcomes: ...

The existing ``scenarios/trench_demo.yaml`` carries extra fields
(``phase_c_pessimism``, ``references``, ``notes``); these are
accepted only at the top level when explicitly declared on
``Scenario``. The ``extra="forbid"`` discipline applies per-model
to fields the loader is responsible for; ad-hoc top-level
sections that are descriptive-only (``phase_c_pessimism``,
``references``) are absorbed onto ``Scenario`` as opaque dicts so
existing scenario files stay loadable.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AnyUrl, BaseModel, ConfigDict, Field, model_validator
from rfmesh_contracts import (
    ArrayConfig,
    ArrayGeometry,
    BearerConfig,
    BearerKind,
    Capability,
    EmitterClass,
    FusionConfig,
    NodeConfig,
    SDRConfig,
)
from rfmesh_contracts.geospatial import GeodeticPosition

# Default fusion endpoint used when a node spec does not pin one.
# Loopback UDP is the in-process orchestrator default: every node
# sends to the same in-memory bus, so the URL is not actually
# opened on the wire by the in-process bearer.
_DEFAULT_FUSION_ENDPOINT: str = "udp://127.0.0.1:9000"

# Default fusion listen URL when the YAML omits the fusion section.
_DEFAULT_FUSION_LISTEN_URL: str = "udp://0.0.0.0:9000"


# ---------------------------------------------------------------------------
# Geometry blocks
# ---------------------------------------------------------------------------


class EnuOrigin(BaseModel):
    """The geodetic anchor of the scenario's local ENU frame.

    Every per-node and emitter position in the scenario is expressed
    in metres east / north of this point. ``hae_m`` is the up-axis
    zero.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lat_deg: float = Field(ge=-90.0, le=90.0)
    lon_deg: float = Field(ge=-180.0, le=180.0)
    hae_m: float = 0.0


class EmitterSpec(BaseModel):
    """The emitter the demo is geolocating.

    Carries enough information for the orchestrator to drive the
    simulator (range / bearing / freq / power) and for the dashboard
    to label the marker. ``expected_class`` is optional metadata that
    the L3 classifier (when present) should produce for an honesty
    test.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    emitter_id: str = Field(min_length=1)
    enu_position_m: tuple[float, float] = Field(
        description="Emitter (east, north) in metres in the scenario ENU frame.",
    )
    center_freq_hz: float = Field(gt=0.0)
    modulation: Literal["cw"] = Field(
        default="cw",
        description=(
            "v1.0 supports only continuous-wave (CW) emitters. LoRa "
            "chirp / FHSS arrive in later tickets driven by L3 needs."
        ),
    )
    emit_power_db_above_noise: float = Field(
        description=(
            "Emitter power as dB above the simulator's noise floor "
            "reference. Per-node SNR is derived by the channel model."
        ),
    )
    expected_class: EmitterClass | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Channel model (parsed; orchestrator reifies into rfmesh_sdr.simulator types)
# ---------------------------------------------------------------------------


class ChannelModelSpec(BaseModel):
    """A free-form description of the simulator channel.

    The orchestrator inspects ``kind`` and reifies one of
    ``rfmesh_sdr.simulator``'s ``ChannelModel`` implementations. The
    fields not explicitly declared here are carried in
    ``ChannelModelSpec.params`` as a passthrough -- the loader's job
    is to keep the shape recognisable, not to validate every channel
    parameter (the rfmesh-sdr layer does that when it constructs the
    real ``ChannelModel``).
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    kind: Literal[
        "free_space",
        "two_ray_ground",
        "multipath_fir",
        "log_normal_shadowing",
        "composite",
    ] = Field(default="free_space")

    # Composite-only -- a list of nested channel specs.
    layers: tuple[ChannelModelSpec, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable dict suitable for ``ReplayMetadata``."""
        return self.model_dump(mode="python", exclude_none=True)


# ---------------------------------------------------------------------------
# Per-node spec
# ---------------------------------------------------------------------------


class _SDRSpec(BaseModel):
    """A minimal SDR block accepted in the scenario YAML.

    Reshaped into ``rfmesh_contracts.SDRConfig`` by ``NodeReplaySpec.
    to_node_config``. Carried here to absorb additional simulator-
    side keys (``driver: sim``) that ``SDRConfig`` already accepts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    driver: Literal["rtlsdr", "hackrf", "bladerf", "pluto", "sim"] = "sim"
    sample_rate_hz: float = Field(gt=0.0, default=2.0e6)
    center_freq_hz: float = Field(gt=0.0, default=915.0e6)
    gain_db: float | Literal["auto"] = 30.0
    bias_tee: bool = False


class _ArraySpec(BaseModel):
    """A minimal array block for L2 nodes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    geometry: Literal["ula", "uca", "custom"] = "ula"
    n_elements: int = Field(ge=2)
    element_spacing_m: float | None = None
    element_positions_m: tuple[tuple[float, float], ...] | None = None
    calibration_file: str | None = None


class _BearerSpec(BaseModel):
    """A minimal bearer block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["wifi", "lora", "both"] = "wifi"
    lora_serial_port: str | None = None
    heartbeat_interval_s: float = Field(gt=0.0, default=2.0)


class NodeReplaySpec(BaseModel):
    """One node's place in the scenario.

    Carries ENU geometry (for the simulator) + everything a
    ``NodeConfig`` needs (for the orchestrator's ``Node`` wiring).
    The ``to_node_config`` method assembles the contracts-side
    ``NodeConfig`` from these fields plus the scenario's ENU origin.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    enu_position_m: tuple[float, float] = Field(
        description="(east, north) metres in the scenario ENU frame.",
    )
    sigma_m: float = Field(
        ge=0.0,
        default=5.0,
        description="Surveyed-position uncertainty for the node, metres.",
    )
    heading_deg: float | None = Field(default=None, ge=0.0, lt=360.0)
    capabilities: tuple[Capability, ...] = Field(min_length=1)
    sdr: _SDRSpec = _SDRSpec()
    array: _ArraySpec | None = None
    bearer: _BearerSpec = _BearerSpec()

    # Demo-narrative annotations (carried, not enforced).
    expected_sigma_deg: float | None = Field(default=None, ge=0.0)
    notes: str | None = None

    # Replay path -- if set, the orchestrator should drive this node
    # via the recorded IQ at this path instead of the live simulator.
    # Kept here so a scenario can pin per-node recordings; the
    # SyntheticReceiver replay-mode extension lands in WS-A-005 (see
    # the README), at which point the orchestrator honours this.
    replay_iqx_path: str | None = None

    @model_validator(mode="after")
    def _validate_l2_array(self) -> NodeReplaySpec:
        """Mirror NodeConfig's L2 -> array cross-check."""
        l2_caps = {Capability.L2_MUSIC, Capability.L2_CAPON, Capability.L2_MVDR_NULL}
        declares_l2 = bool(set(self.capabilities) & l2_caps)
        if declares_l2 and self.array is None:
            msg = (
                f"NodeReplaySpec {self.node_id!r} declares an L2 capability "
                "but has no 'array' block."
            )
            raise ValueError(msg)
        if not declares_l2 and self.array is not None:
            msg = (
                f"NodeReplaySpec {self.node_id!r} has an array block but no L2 capability declared."
            )
            raise ValueError(msg)
        return self

    def to_node_config(
        self,
        origin: EnuOrigin,
        *,
        fusion_endpoint: str | None = None,
    ) -> NodeConfig:
        """Assemble a contracts-side ``NodeConfig`` from this spec.

        The geodetic ``position`` is derived by treating the scenario
        origin as a flat-Earth anchor and converting (east, north) to
        a degree offset using the small-region approximation (1° lat
        ~= 111_320 m at the WGS-84 reference). This matches the
        ``rfmesh-cot`` equirectangular projection convention -- correct
        to < 1 m within the BoTH3 demo's 5 km radius and within
        the precision the orchestrator needs.
        """
        east_m, north_m = self.enu_position_m
        lat_deg = origin.lat_deg + (north_m / 111_320.0)
        # Longitude scales with cos(lat); using the origin latitude
        # is good enough for a < 10 km neighbourhood.
        cos_lat = math.cos(math.radians(origin.lat_deg))
        lon_deg = origin.lon_deg + (east_m / (111_320.0 * max(cos_lat, 1e-6)))

        # SDRConfig + BearerConfig + ArrayConfig assembly.
        sdr_cfg = SDRConfig(
            driver=self.sdr.driver,
            sample_rate_hz=self.sdr.sample_rate_hz,
            center_freq_hz=self.sdr.center_freq_hz,
            gain_db=self.sdr.gain_db,
            bias_tee=self.sdr.bias_tee,
        )
        bearer_cfg = BearerConfig(
            kind=BearerKind(self.bearer.kind),
            lora_serial_port=self.bearer.lora_serial_port,
            heartbeat_interval_s=self.bearer.heartbeat_interval_s,
        )
        array_cfg: ArrayConfig | None = None
        if self.array is not None:
            array_cfg = ArrayConfig(
                geometry=ArrayGeometry(self.array.geometry),
                n_elements=self.array.n_elements,
                element_spacing_m=self.array.element_spacing_m,
                element_positions_m=self.array.element_positions_m,
                calibration_file=self.array.calibration_file,
            )

        return NodeConfig(
            node_id=self.node_id,
            position=GeodeticPosition(
                lat_deg=lat_deg,
                lon_deg=lon_deg,
                hae_m=origin.hae_m,
                sigma_m=self.sigma_m,
            ),
            heading_deg=self.heading_deg,
            sdr=sdr_cfg,
            array=array_cfg,
            capabilities=self.capabilities,
            bearer=bearer_cfg,
            fusion_endpoint=AnyUrl(fusion_endpoint or _DEFAULT_FUSION_ENDPOINT),
        )


# ---------------------------------------------------------------------------
# Demo beats + expected outcomes
# ---------------------------------------------------------------------------


class ExpectedFix(BaseModel):
    """Honesty test target for a single beat.

    The orchestrator does not enforce these at runtime; they are
    carried so the demo-replay regression test harness can assert
    that the fused output matches the geometry spec within a
    tolerance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    semi_major_m: float = Field(gt=0.0)
    semi_minor_m: float = Field(gt=0.0)
    orientation_deg: float = Field(ge=-180.0, le=180.0)
    range_m: float = Field(gt=0.0)
    semi_over_range_pct: float = Field(ge=0.0)
    gdop: float = Field(gt=0.0)
    confidence_level: Literal["HIGH", "MEDIUM", "LOW"]
    # Optional narrative-honesty fields.
    semi_minor_m_drop_pct: float | None = None


class DemoBeatSpec(BaseModel):
    """One beat in the demo's sequenced narrative.

    A beat is a window of wall-clock time during which a specific
    subset of nodes is "online". The orchestrator transitions
    between beats by enabling / disabling each node's bearer.
    Offline nodes are dropped from new fixes by the fusion server's
    ``node_stale_after_s`` gate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    beat_id: str = Field(min_length=1)
    label: str = ""
    duration_s: float = Field(gt=0.0, default=10.0)
    active_nodes: tuple[str, ...] = Field(min_length=1)
    expected_fix: ExpectedFix | None = None
    dashboard_caption: str = ""


class ExpectedOutcomes(BaseModel):
    """Top-level honesty targets for the whole scenario.

    Carries pre-aggregated bounds the honest replay test can check
    against (e.g. ``l1_only_gdop_max``). Optional.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    l1_only_gdop_max: float | None = None
    l1_plus_l2_gdop_max: float | None = None
    l1_only_semi_major_m_max: float | None = None
    l1_plus_l2_semi_major_m_max: float | None = None
    l1_plus_l2_semi_minor_m_max: float | None = None


class FusionSpec(BaseModel):
    """Fusion-server parameters for the scenario.

    Maps cleanly to ``rfmesh_contracts.FusionConfig`` via
    ``to_fusion_config``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    listen_url: str = _DEFAULT_FUSION_LISTEN_URL
    batch_window_ms: float = Field(gt=0.0, default=100.0)
    node_stale_after_s: float = Field(gt=0.0, default=6.0)
    min_bearings_for_fix: int = Field(ge=2, default=2)
    gdop_warn_threshold: float = Field(gt=0.0, default=6.0)
    cot_url: str | None = None

    def to_fusion_config(self) -> FusionConfig:
        """Reshape into the contracts-side ``FusionConfig``."""
        return FusionConfig(
            listen_url=AnyUrl(self.listen_url),
            batch_window_ms=self.batch_window_ms,
            node_stale_after_s=self.node_stale_after_s,
            min_bearings_for_fix=self.min_bearings_for_fix,
            gdop_warn_threshold=self.gdop_warn_threshold,
            cot_url=AnyUrl(self.cot_url) if self.cot_url is not None else None,
        )


# ---------------------------------------------------------------------------
# Top-level Scenario
# ---------------------------------------------------------------------------


class Scenario(BaseModel):
    """A complete loaded demo scenario.

    Top-level model produced by ``ScenarioLoader.load``. Composes
    the geometry, the emitter, the channel, the nodes, the beats,
    and the fusion configuration. Extras at the top level are
    allowed -- the existing ``scenarios/trench_demo.yaml`` carries
    ``phase_c_pessimism`` and ``references`` blocks the loader
    does not consume but should not reject.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    scenario_id: str = Field(min_length=1)
    description: str = ""
    enu_origin: EnuOrigin
    emitter: EmitterSpec
    channel_model: ChannelModelSpec = ChannelModelSpec()
    nodes: tuple[NodeReplaySpec, ...] = Field(min_length=1)
    beats: tuple[DemoBeatSpec, ...] = Field(default_factory=tuple)
    fusion: FusionSpec = FusionSpec()
    expected_outcomes: ExpectedOutcomes | None = None

    @model_validator(mode="after")
    def _validate_beats_reference_known_nodes(self) -> Scenario:
        """Each beat's active_nodes must be a subset of the scenario's nodes."""
        known = {n.node_id for n in self.nodes}
        for beat in self.beats:
            missing = set(beat.active_nodes) - known
            if missing:
                msg = (
                    f"Demo beat {beat.beat_id!r} references unknown "
                    f"nodes: {sorted(missing)}; known nodes are "
                    f"{sorted(known)}."
                )
                raise ValueError(msg)
        return self

    def node_by_id(self, node_id: str) -> NodeReplaySpec:
        """Return the ``NodeReplaySpec`` matching ``node_id`` or raise KeyError."""
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        msg = f"Scenario has no node with id {node_id!r}."
        raise KeyError(msg)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class ScenarioLoader:
    """Loads a scenario YAML into a fully-populated ``Scenario`` object.

    Accepts the YAML shape in ``scenarios/trench_demo.yaml`` (the
    "design intent" YAML the architect references); the loader
    absorbs YAML-side ergonomics (e.g. ``demo_beats`` -> ``beats``
    rename, ``capabilities`` as a list of string enum values) and
    produces the strict Pydantic ``Scenario``.

    Two responsibilities, kept distinct so the failure modes are
    clear in tests:

    * **Parse**: load the YAML bytes and convert to a dict. A
      malformed file is a ``yaml.YAMLError``.
    * **Validate**: hand the dict to ``Scenario.model_validate``. A
      schema mismatch is a ``pydantic.ValidationError`` -- the loud
      "extra='forbid' typo'd field" surface.

    No silent fallbacks: if either step fails, the exception
    propagates to the caller (and to the CLI, which prints it).
    """

    def load(self, yaml_path: Path) -> Scenario:
        """Load the YAML at ``yaml_path`` and return a validated ``Scenario``.

        Raises:
            FileNotFoundError: if ``yaml_path`` does not exist.
            yaml.YAMLError: on malformed YAML.
            pydantic.ValidationError: on schema mismatch.
        """
        raw = yaml_path.read_text(encoding="utf-8")
        payload = yaml.safe_load(raw)
        if not isinstance(payload, Mapping):
            msg = (
                f"ScenarioLoader: expected a YAML mapping at the top level "
                f"of {yaml_path}, got {type(payload).__name__}."
            )
            raise TypeError(msg)
        reshaped = _reshape_yaml_payload(dict(payload))
        return Scenario.model_validate(reshaped)


def _reshape_yaml_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert the human-friendly YAML shape to the loader's strict shape.

    Small bridges so the existing ``scenarios/trench_demo.yaml``
    (design-intent) round-trips through the loader cleanly:

    1. ``demo_beats`` (YAML, descriptive) -> ``beats`` (loader, strict).
    2. ``channel`` (YAML) -> ``channel_model`` (loader).
    3. ``emitter.expected_class`` upper-case YAML strings (``UNKNOWN``)
       -> lower-case enum values (``unknown``).
    4. Top-level descriptive sections that the loader ignores
       (``phase_c_pessimism``, ``references``) survive as opaque
       fields because ``Scenario.extra='allow'``; we strip
       per-node ``notes`` and free-form keys only where the
       per-node model is strict.
    """
    out = dict(payload)
    if "demo_beats" in out and "beats" not in out:
        out["beats"] = out.pop("demo_beats")
    if "channel" in out and "channel_model" not in out:
        out["channel_model"] = out.pop("channel")
    emitter = out.get("emitter")
    if isinstance(emitter, dict):
        emitter = dict(emitter)
        ec = emitter.get("expected_class")
        if isinstance(ec, str):
            emitter["expected_class"] = ec.lower()
        out["emitter"] = emitter
    # Normalise beat ``confidence_level`` to upper-case (the
    # ``ExpectedFix`` model carries ``Literal["HIGH","MEDIUM","LOW"]``
    # in upper case; tolerate lower-case input).
    beats_raw = out.get("beats")
    if isinstance(beats_raw, list):
        rebuilt: list[dict[str, Any]] = []
        for beat in beats_raw:
            if not isinstance(beat, dict):
                rebuilt.append(beat)
                continue
            beat_dict = dict(beat)
            exp = beat_dict.get("expected_fix")
            if isinstance(exp, dict):
                exp = dict(exp)
                lvl = exp.get("confidence_level")
                if isinstance(lvl, str):
                    exp["confidence_level"] = lvl.upper()
                beat_dict["expected_fix"] = exp
            rebuilt.append(beat_dict)
        out["beats"] = rebuilt
    return out


__all__ = [
    "ChannelModelSpec",
    "DemoBeatSpec",
    "EmitterSpec",
    "EnuOrigin",
    "ExpectedFix",
    "ExpectedOutcomes",
    "FusionSpec",
    "NodeReplaySpec",
    "Scenario",
    "ScenarioLoader",
]
