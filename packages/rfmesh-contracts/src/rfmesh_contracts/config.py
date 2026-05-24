"""Configuration schemas -- the declarative description of a deployment.

Every rfmesh process is configured by a YAML file that validates into one of
the models here. Nothing in the codebase hard-codes a frequency, a node count,
an antenna spacing, or a server address: it is *all* config. That is what lets
the same software absorb the on-site uncertainty -- you do not know until you
arrive whether you will have one bladeRF or three, whether the partner pool
yields a uniform linear or a circular array, how many nodes you will field.
You express what you got in YAML; the code adapts.

WHERE THE THREE AXES OF VARIABILITY ARE ABSORBED
------------------------------------------------
The architecture has three orthogonal axes of variability, and each is
absorbed in exactly one place so it never leaks into DSP or fusion logic:

* Axis 1 -- SDR hardware type. Absorbed by ``SDRConfig.driver`` plus the SDR
  workstream's ``Receiver`` protocol implementations. DSP code receives IQ; it
  never branches on whether the IQ came from an RTL-SDR or a bladeRF.
* Axis 2 -- per-node capability. Absorbed by ``NodeConfig.capabilities`` plus
  the node runtime's startup capability-detection. The node builds the right
  processing pipeline; fusion just receives BearingReports tagged with a
  ``method``.
* Axis 3 -- node count. Absorbed by there simply being N ``NodeConfig`` files
  and the fusion solver accepting any N >= 2 bearings. There is no "expected
  number of nodes" constant anywhere.

VALIDATION PHILOSOPHY
---------------------
``extra="forbid"`` everywhere: a typo'd or stale YAML key is a *loud* error at
load time, not a silently ignored setting that makes you debug a "working"
config for an hour on-site. Cross-field invariants (an L2 capability requires
an array; a custom geometry requires explicit element positions) are enforced
by validators here so a malformed deployment is rejected before any hardware
is touched. This is the contract-layer half of Invariant 4 ("No silent
fallbacks"); the node runtime enforces the other half (declared capability
not supported by detected hardware -> fatal).
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from .enums import ArrayGeometry, BearerKind, Capability
from .geospatial import GeodeticPosition
from .version import SCHEMA_VERSION, SchemaVersionT


class SDRConfig(BaseModel):
    """Which radio a node uses and how it is tuned. (Axis 1: SDR hardware type.)

    ``driver`` is the only place in a node's configuration that names a
    hardware family. The SDR workstream maps each ``driver`` value to a
    ``Receiver`` (or ``CoherentReceiver``) implementation -- SoapySDR-backed
    for the generic single-channel path, native-library-backed for the
    phase-coherent path. Everything downstream of the ``Receiver`` protocol is
    driver-agnostic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    driver: Literal["rtlsdr", "hackrf", "bladerf", "pluto", "sim"] = Field(
        description=(
            "SDR hardware family. 'rtlsdr'/'hackrf' are single-channel "
            "(L1-capable only). 'bladerf'/'pluto' expose a phase-coherent "
            "2-RX mode (L1- and L2-capable). 'sim' is the synthetic receiver "
            "from the SDR workstream -- the same Receiver protocol backed by "
            "generated IQ, so the entire pipeline runs and is tested with no "
            "hardware at all."
        ),
    )
    serial: str | None = Field(
        default=None,
        description=(
            "Hardware serial / identifier to disambiguate when several SDRs "
            "of the same driver are attached to one host. None means 'the "
            "first device this driver finds'. Ignored for driver='sim'."
        ),
    )
    sample_rate_hz: float = Field(
        gt=0.0,
        description=(
            "Requested ADC sample rate, Hz. The achievable rate is "
            "driver-dependent (e.g. RTL-SDR is reliable to ~2.4 MS/s, bladeRF "
            "far higher); the Receiver implementation reports the *actual* "
            "rate it obtained, and a meaningful shortfall is surfaced, not "
            "hidden."
        ),
    )
    center_freq_hz: float = Field(
        gt=0.0,
        description=(
            "Tuner centre frequency, Hz. For a coherent 2-RX device both "
            "channels share this LO -- which is precisely the property that "
            "makes them phase-coherent, so it is correct that there is one "
            "value here, not two."
        ),
    )
    gain_db: float | Literal["auto"] = Field(
        description=(
            "Receiver gain in dB, or the string 'auto' for the driver's AGC. "
            "Prefer an explicit number for DF work: a fixed, known gain keeps "
            "the noise floor stable across an antenna sweep, which matters for "
            "honest SNR and sigma estimates. 'auto' is a convenience for "
            "bring-up, not for measurement."
        ),
    )
    bias_tee: bool = Field(
        default=False,
        description=(
            "Whether to enable the SDR's bias-tee (DC on the antenna port) to "
            "power an active antenna or an inline LNA. Default False: enabling "
            "it into hardware that does not expect DC is a way to release "
            "magic smoke, so it must be opted into explicitly."
        ),
    )


class ArrayConfig(BaseModel):
    """Physical antenna-array description for a phase-coherent L2 node.

    Present only on nodes that do L2 (MUSIC / MVDR). The DSP workstream builds
    its steering-vector / array-manifold model from this; it is the bridge
    between 'what antennas are physically bolted to the mast' and the
    subspace maths. ``None`` on an L1-only node (see ``NodeConfig`` validator).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    geometry: ArrayGeometry = Field(
        description=(
            "Array layout: ULA, UCA, or CUSTOM. Selects the steering-vector "
            "model. See ArrayGeometry for the trade-offs (notably ULA's "
            "front/back ambiguity vs UCA's full 360 deg coverage)."
        ),
    )
    n_elements: int = Field(
        ge=2,
        description=(
            "Number of antenna elements / coherent RX channels. >= 2 (one "
            "channel cannot do phase DF). With a 2-RX device this is 2; a "
            "KrakenSDR-style rig would be 5."
        ),
    )
    element_spacing_m: float | None = Field(
        default=None,
        description=(
            "Inter-element spacing in metres, for the regular geometries "
            "(ULA: spacing between adjacent elements; UCA: radius). Typically "
            "~half a wavelength at the design frequency (e.g. ~0.164 m at "
            "915 MHz). Required for ULA/UCA, must be None for CUSTOM (use "
            "element_positions_m instead). Enforced by the validator below."
        ),
    )
    element_positions_m: tuple[tuple[float, float], ...] | None = Field(
        default=None,
        description=(
            "Explicit (x, y) element coordinates in metres in the array's "
            "local frame, for geometry=CUSTOM. Length must equal n_elements. "
            "Must be None for ULA/UCA. The escape hatch for whatever physical "
            "arrangement the on-site hardware actually permits."
        ),
    )
    calibration_file: str | None = Field(
        default=None,
        description=(
            "Path to a per-element phase/gain calibration table (frequency- "
            "dependent complex offsets) produced by the SDR workstream's "
            "calibration routine. None means 'not yet calibrated' -- and an "
            "L2 node that is asked to run MUSIC without a calibration must "
            "fail loudly, not silently emit garbage bearings (Invariant 4)."
        ),
    )

    @model_validator(mode="after")
    def _geometry_consistency(self) -> ArrayConfig:
        """Enforce that the geometry and the way elements are described agree.

        ULA/UCA are *parametric* -- they need a single ``element_spacing_m``
        and must not also carry an explicit position list (which would be a
        second, possibly contradictory, source of truth). CUSTOM is the
        opposite: it needs the explicit list and no scalar spacing. Catching
        this here means the DSP workstream can trust the invariant and never
        defensively re-check it.
        """
        if self.geometry in (ArrayGeometry.ULA, ArrayGeometry.UCA):
            if self.element_spacing_m is None:
                msg = (
                    f"ArrayConfig.geometry={self.geometry.value} requires "
                    "element_spacing_m to be set."
                )
                raise ValueError(msg)
            if self.element_positions_m is not None:
                msg = (
                    f"ArrayConfig.geometry={self.geometry.value} is parametric; "
                    "element_positions_m must be None (use element_spacing_m)."
                )
                raise ValueError(msg)
        else:  # ArrayGeometry.CUSTOM
            if self.element_positions_m is None:
                msg = (
                    "ArrayConfig.geometry=custom requires element_positions_m "
                    "to be supplied explicitly."
                )
                raise ValueError(msg)
            if self.element_spacing_m is not None:
                msg = (
                    "ArrayConfig.geometry=custom must not set element_spacing_m "
                    "(positions are given explicitly in element_positions_m)."
                )
                raise ValueError(msg)
            if len(self.element_positions_m) != self.n_elements:
                msg = (
                    "ArrayConfig.element_positions_m has "
                    f"{len(self.element_positions_m)} entries but n_elements "
                    f"is {self.n_elements}; they must match."
                )
                raise ValueError(msg)
        return self


class BearerConfig(BaseModel):
    """Transport configuration for a node's link to the fusion server.

    Absorbed entirely by the node-runtime workstream's transport layer. The
    contract carries it only because a ``NodeConfig`` must declare how the
    node talks home.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: BearerKind = Field(
        description=(
            "WIFI (UDP+msgpack, primary, can carry debug payloads), LORA "
            "(compressed essential fields only, EW-resilient fallback), or "
            "BOTH (Wi-Fi preferred, LoRa hot standby; fusion de-duplicates)."
        ),
    )
    lora_serial_port: str | None = Field(
        default=None,
        description=(
            "Serial device for the LoRa radio (e.g. '/dev/ttyUSB0'). Required "
            "when kind is LORA or BOTH; must be None for WIFI-only. Enforced "
            "by the validator below."
        ),
    )
    heartbeat_interval_s: float = Field(
        default=2.0,
        gt=0.0,
        description=(
            "How often the node emits a NodeStatus heartbeat, seconds. The "
            "fusion staleness window is a small multiple of this; a node "
            "silent for that long is dropped from new fixes."
        ),
    )

    @model_validator(mode="after")
    def _lora_needs_port(self) -> BearerConfig:
        """A LoRa-using bearer must say which serial port the radio is on.

        And a Wi-Fi-only bearer must *not* carry a stray LoRa port, because a
        leftover value from a copy-pasted config would imply hardware that is
        not there. Both directions are checked.
        """
        uses_lora = self.kind in (BearerKind.LORA, BearerKind.BOTH)
        if uses_lora and self.lora_serial_port is None:
            msg = f"BearerConfig.kind={self.kind.value} uses LoRa but lora_serial_port is not set."
            raise ValueError(msg)
        if not uses_lora and self.lora_serial_port is not None:
            msg = "BearerConfig.kind=wifi must not set lora_serial_port (no LoRa radio is in use)."
            raise ValueError(msg)
        return self


class NodeConfig(BaseModel):
    """The complete configuration of one mesh node. (Axes 1 & 2 converge here.)

    One YAML file per node. The node runtime loads it, detects the attached
    SDR, intersects ``capabilities`` (what the operator *wants* this node to
    do) with what the hardware *can* do, and builds the processing pipeline
    accordingly. A declared capability the hardware cannot support is a fatal
    startup error -- never a silent downgrade.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: SchemaVersionT = Field(
        default=SCHEMA_VERSION,
        description="Contract version this config targets; pinned to SCHEMA_VERSION.",
    )
    node_id: str = Field(
        min_length=1,
        description=(
            "Stable, unique node identifier, e.g. 'node-rtl-01'. Appears in "
            "every BearingReport and NodeStatus this node emits. Uniqueness "
            "across the mesh is the operator's responsibility."
        ),
    )
    position: GeodeticPosition = Field(
        description=(
            "The node's surveyed/known position. A node with GNSS overrides "
            "this at runtime from its fix; this config value is the fallback "
            "and the bench/sim value. Accurate node positions matter -- "
            "position error propagates straight into fix error."
        ),
    )
    heading_deg: float | None = Field(
        default=None,
        ge=0.0,
        lt=360.0,
        description=(
            "Mounting heading of a directional antenna / array boresight, "
            "degrees true, clockwise from north. Required for an L1 node (the "
            "servo sweep is relative to this) and for a ULA L2 node (the array "
            "axis orientation). May be None for a UCA node (circular arrays "
            "are rotationally symmetric). The node runtime checks this against "
            "capabilities at startup."
        ),
    )
    sdr: SDRConfig = Field(
        description="The radio this node uses and how it is tuned. (Axis 1.)",
    )
    array: ArrayConfig | None = Field(
        default=None,
        description=(
            "Antenna-array description. Required iff the node declares an L2 "
            "capability; must be None for an L1-only node. Enforced below."
        ),
    )
    capabilities: tuple[Capability, ...] = Field(
        min_length=1,
        description=(
            "What this node is configured to do (Axis 2). The operator's "
            "intent. Intersected at startup with hardware-detected capability; "
            "a declared capability the hardware cannot meet is fatal. At least "
            "one capability must be declared."
        ),
    )
    bearer: BearerConfig = Field(
        description="How the node ships BearingReports and NodeStatus to fusion.",
    )
    fusion_endpoint: AnyUrl = Field(
        description=(
            "Where this node sends its reports -- the fusion server's address "
            "(e.g. 'udp://10.0.0.1:9000'). The node is otherwise unaware of "
            "the rest of the mesh; the star topology keeps nodes stateless "
            "about each other."
        ),
    )

    @model_validator(mode="after")
    def _capabilities_consistency(self) -> NodeConfig:
        """Enforce the structural preconditions of the declared capabilities.

        This is config-time validation only -- it confirms the *config itself*
        is internally coherent (an L2 capability is meaningless without an
        ``array`` block; an L1/ULA node needs a ``heading_deg``). It explicitly
        does **not** check that the hardware can deliver the capability -- that
        requires a connected SDR and is the node runtime's job at startup.
        Two layers, two responsibilities, both loud on failure.
        """
        caps = set(self.capabilities)
        l2_caps = {
            Capability.L2_MUSIC,
            Capability.L2_CAPON,
            Capability.L2_MVDR_NULL,
        }
        declares_l2 = bool(caps & l2_caps)

        if declares_l2 and self.array is None:
            msg = (
                "NodeConfig declares an L2 capability "
                f"({sorted(c.value for c in caps & l2_caps)}) but has no "
                "'array' block; L2 requires a phase-coherent antenna array."
            )
            raise ValueError(msg)
        if not declares_l2 and self.array is not None:
            msg = (
                "NodeConfig has an 'array' block but declares no L2 "
                "capability; remove the array or add an L2 capability."
            )
            raise ValueError(msg)

        # An L1 node steers a directional antenna by servo; a ULA L2 node has
        # an oriented array axis. Both need to know their heading. A UCA L2
        # node does not (circular symmetry).
        needs_heading = Capability.L1_RSSI in caps or (
            declares_l2 and self.array is not None and self.array.geometry is ArrayGeometry.ULA
        )
        if needs_heading and self.heading_deg is None:
            msg = (
                "NodeConfig requires heading_deg (it declares L1_RSSI and/or a "
                "ULA L2 array, both of which are orientation-dependent)."
            )
            raise ValueError(msg)
        return self


class FusionConfig(BaseModel):
    """Configuration of the central fusion server. (Axis 3 lives in its absence.)

    Note what is *not* here: any notion of "the expected number of nodes".
    The fusion solver accepts whatever bearings arrive in a time window and
    cross-fixes any N >= 2 of them. Node count is a deployment fact, not a
    configured constant -- that is how Axis 3 (node-count variability) is
    absorbed: by the architecture simply not caring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: SchemaVersionT = Field(
        default=SCHEMA_VERSION,
        description="Contract version this config targets; pinned to SCHEMA_VERSION.",
    )
    listen_url: AnyUrl = Field(
        description=(
            "Where the fusion server receives node reports, e.g. "
            "'udp://0.0.0.0:9000'. Must match the nodes' fusion_endpoint."
        ),
    )
    batch_window_ms: float = Field(
        default=100.0,
        gt=0.0,
        description=(
            "Width of the time window over which BearingReports are grouped "
            "before solving a fix, milliseconds. Wide enough to gather all "
            "nodes' reports for one 'instant', narrow enough that a moving "
            "emitter has not travelled far within it. ~100 ms is the start "
            "point; tune against the demo scenario."
        ),
    )
    node_stale_after_s: float = Field(
        default=6.0,
        gt=0.0,
        description=(
            "If no NodeStatus/BearingReport has arrived from a node within "
            "this many seconds, fusion treats it as stale and excludes it from "
            "new fixes (and the dashboard shows it greyed out). A small "
            "multiple of the nodes' heartbeat_interval_s. This is what makes "
            "the 'pull a node mid-demo, watch the ellipse grow' robustness "
            "story work."
        ),
    )
    min_bearings_for_fix: int = Field(
        default=2,
        ge=2,
        description=(
            "Minimum BearingReports required to attempt a fix. Two bearings "
            "give a single (elongated) ellipse; three+ are over-determined and "
            "yield residuals for self-diagnosis. Hard floor of 2 -- one bearing "
            "is a ray, not a fix."
        ),
    )
    gdop_warn_threshold: float = Field(
        default=6.0,
        gt=0.0,
        description=(
            "GDOP above this value flags the fix as geometrically weak: "
            "fusion will not raise confidence_level above MEDIUM and the "
            "dashboard warns the operator to reposition nodes. A reporting/"
            "display threshold, not a hard reject -- a weak fix is still "
            "information, honestly labelled."
        ),
    )
    cot_url: AnyUrl | None = Field(
        default=None,
        description=(
            "Where the CoT workstream publishes FixEvents as Cursor-on-Target "
            "(e.g. a FreeTAKServer 'tcp://10.0.0.2:8087'). None disables CoT "
            "output -- useful for a headless bench run where only the ops "
            "dashboard is watched."
        ),
    )


class CommsConfig(BaseModel):
    """DSSS directional-comms physical-layer configuration (ADR-025).

    Carries only **cross-workstream** parameters: every value below is
    referenced by at least two of {``rfmesh-dsss`` DSP, ``rfmesh-sdr``
    drivers, ``rfmesh-node`` comms loop}. Node-layer concerns (peer
    roster, routing table, per-link policy, TDD slot assignment) live
    in ``rfmesh-node/comms/comms_config.py`` as a ``RendezvousConfig``-
    style helper -- they do not touch frozen contracts (mirror of how
    Rendezvous is structured).

    Required when ``NodeConfig.capabilities`` contains
    ``Capability.COMMS_DSSS``. Cross-validation against ``SDRConfig``
    (``chip_rate_hz`` <= ``sdr.sample_rate_hz``) is performed at node
    startup, not here, because the realised SDR sample rate may
    differ from the requested one (see
    ``ReceiverCapabilities.actual_sample_rate_hz`` /
    ``TransmitterCapabilities.actual_sample_rate_hz``).

    Added in SCHEMA_VERSION 1.3.0.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    carrier_freq_hz: float = Field(
        gt=0.0,
        description=(
            "RF carrier frequency for the DSSS link, Hz. Independent of "
            "any DF carrier configured on the same node -- DF and COMMS "
            "modes are mutually exclusive in v1.3.0 so the same SDR LO "
            "serves whichever mode is active. The ATK-10 Yagi covers "
            "868-915 MHz; a typical European deployment value is "
            "868e6 or 915e6."
        ),
    )
    chip_rate_hz: float = Field(
        gt=0.0,
        description=(
            "DSSS chip rate, Hz. Target ~10e6 (10 Mchip/s) over a "
            "BPSK spread of length 1023 yields a payload symbol rate "
            "of ~9.77 ksym/s and a processing gain of 10*log10(1023) "
            "~= 30 dB. Must not exceed the SDR's actual sample rate; "
            "cross-checked at node startup against "
            "ReceiverCapabilities/TransmitterCapabilities."
        ),
    )
    spreading_factor: int = Field(
        default=1023,
        ge=3,
        description=(
            "Number of chips per data symbol. Must equal 2**n - 1 for "
            "the LFSR-tap polynomial that produces a maximal-length "
            "m-sequence; validated against lfsr_taps. Default 1023 = "
            "2**10 - 1, the canonical length-10 m-sequence used by "
            "the BoTH3 build."
        ),
    )
    lfsr_taps: tuple[int, ...] = Field(
        description=(
            "Tap positions for the LFSR generating the m-sequence "
            "(1-indexed feedback taps, smallest tap first). For "
            "length-10 (spreading_factor=1023) the canonical "
            "primitive polynomial is x^10 + x^3 + 1, encoded as "
            "(10, 3). The set of valid tap tuples for each register "
            "length is documented in rfmesh_dsss.pn_sequence; the "
            "validator below checks only that the maximum tap "
            "matches log2(spreading_factor + 1)."
        ),
    )
    lfsr_seed: int = Field(
        gt=0,
        description=(
            "Non-zero initial LFSR state (a zero seed is a fixed-point "
            "of the LFSR -- it produces an all-zero sequence, which is "
            "not an m-sequence). Different nodes in the same mesh use "
            "the SAME PN sequence (single shared spreading code in "
            "v1.3.0; per-link codes are deferred); the operator-set "
            "value is the mesh-wide secret."
        ),
    )
    tdd_slot_ms: float = Field(
        gt=0.0,
        description=(
            "Width of one TDD half-duplex slot, milliseconds. The TDD "
            "scheduler assigns TX-on/RX-on slots per link; loose NTP "
            "synchronisation (~10 ms) is hidden inside the guard "
            "interval below. Typical value: 100-500 ms for the v1.3.0 "
            "skirmishing throughput (~10 kbit/s)."
        ),
    )
    tdd_guard_ms: float = Field(
        ge=0.0,
        description=(
            "Guard interval between adjacent TDD slots, milliseconds. "
            "Must comfortably exceed the worst-case inter-node clock "
            "skew (NTP on the rfmesh mesh: ~10 ms) plus RF "
            "settling-time on retune. A generous default (20-50 ms) "
            "trades throughput for robustness; the v1.3.0 link runs "
            "happily on loose timing because the bitrate is modest."
        ),
    )
    frame_payload_max_bytes: int = Field(
        gt=0,
        description=(
            "Maximum payload bytes per DSSS frame. Set so that one "
            "frame fits inside one TDD slot at the configured "
            "chip_rate / spreading_factor / coding overhead. The "
            "framing module enforces this on encode; oversize "
            "payloads must be fragmented at the application layer."
        ),
    )

    @model_validator(mode="after")
    def _spreading_consistency(self) -> CommsConfig:
        """Validate spreading_factor + lfsr_taps shape together.

        Required because the relationship (2**n - 1, max tap position
        == n) is not expressible in single-field Field() constraints.
        """
        sf = self.spreading_factor
        if sf <= 0 or (sf & (sf + 1)) != 0:
            msg = (
                f"spreading_factor must equal 2**n - 1 for some n >= 2 "
                f"(LFSR m-sequence length); got {sf}."
            )
            raise ValueError(msg)
        n_register = (sf + 1).bit_length() - 1
        if not self.lfsr_taps:
            msg = "lfsr_taps must be non-empty."
            raise ValueError(msg)
        if max(self.lfsr_taps) != n_register:
            msg = (
                f"max(lfsr_taps) must equal log2(spreading_factor + 1) "
                f"= {n_register} for an m-sequence of length {sf}; "
                f"got max tap {max(self.lfsr_taps)}."
            )
            raise ValueError(msg)
        if min(self.lfsr_taps) < 1:
            msg = "lfsr_taps are 1-indexed; smallest tap must be >= 1."
            raise ValueError(msg)
        return self
