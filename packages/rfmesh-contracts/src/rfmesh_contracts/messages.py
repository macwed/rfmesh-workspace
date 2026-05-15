"""Frozen inter-package messages -- the wire format between rfmesh workstreams.

Three message types flow across workstream boundaries:

* ``BearingReport``  -- produced by a *node* (DSP workstream's L1/L2 code,
  wrapped by the node runtime); consumed by the *fusion* workstream.
* ``FixEvent``       -- produced by the *fusion* workstream; consumed by the
  *CoT* workstream (and the ops dashboard).
* ``NodeStatus``     -- produced by every *node*; consumed by the ops
  dashboard and by fusion (to know which nodes are alive / stale).

These are the contract. The whole multi-agent build depends on them being
stable: a workstream may freely change *how* it produces or consumes these,
but not *what they are*. Changing a field is an ADR + a ``SCHEMA_VERSION``
bump by the lead -- see ``version.py`` and ``AGENTS.md``.

WHY ``schema_version`` IS A ``Literal``
---------------------------------------
Each message pins ``schema_version: Literal[SCHEMA_VERSION]``. Because
``SCHEMA_VERSION`` is a module-level constant, ``Literal[SCHEMA_VERSION]``
resolves at type-check time. If workstream B is built against contract 1.0.0
and the lead bumps to 1.1.0, B's code that constructs a ``BearingReport`` with
the old literal is now a *type error* under mypy -- the drift is caught before
integration, not during the live demo. This is the single most important
type-system mechanism in the project.

UNITS AND CONVENTIONS RECAP (authoritative; see also ``geospatial.py``)
----------------------------------------------------------------------
* Time: ``t_unix_ns`` is integer nanoseconds since the Unix epoch, UTC.
  Integer, not float -- float64 loses nanosecond resolution past ~2^53 ns
  (~104 days) and we will not debug that on-site.
* Azimuth: degrees, true north = 0, clockwise positive, range [0, 360).
* Angular uncertainty: degrees, 1-sigma.
* Power/SNR: decibels. SNR is dB above the node's own noise floor estimate.
  Absolute power (dBm) is deliberately absent -- none of our SDRs are
  power-calibrated (see ``docs/runbook-demo.md``); claiming dBm to an RF jury
  without a calibration source is a credibility own-goal.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .enums import Capability, ConfidenceLevel, EmitterClass
from .geospatial import EllipseENU, GeodeticPosition
from .version import SCHEMA_VERSION


class BearingReport(BaseModel):
    """One node's estimate of the azimuth to an emitter, at one instant.

    This is the atom of the whole system. A node measures a bearing (by L1
    amplitude sweep or L2 phase-coherent MUSIC), attaches everything the
    fusion solver needs to weight and place it, and ships it. The fusion
    workstream collects ``BearingReport`` s from all nodes within a short time
    window and cross-fixes them into a ``FixEvent``.

    Crucially, the fusion solver is **indifferent to how the bearing was
    produced** -- an 8-degree L1 bearing and a 1.5-degree L2 bearing are the
    same type, distinguished only by ``azimuth_sigma_deg`` and ``method``. The
    weighted least-squares estimator uses ``azimuth_sigma_deg`` as the inverse
    weight. This is exactly what lets the mesh be heterogeneous: cheap RTL-SDR
    L1 nodes and expensive phase-coherent L2 nodes contribute to the *same*
    fix, each according to its honest uncertainty. Absorbing the "what
    hardware / what method" variability into a single scalar weight is the
    design's central move.

    Honesty of ``azimuth_sigma_deg`` is therefore load-bearing. A node that
    reports an over-optimistic sigma poisons the fix for everyone. The DSP
    workstream's sigma estimation is itself tested against the simulator's
    ground truth (see ``WORKSTREAMS.md`` -> Workstream B acceptance criteria).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description=(
            "Contract version this message was built against. Pinned to "
            "SCHEMA_VERSION; a mismatch after a contract bump is caught by "
            "mypy in the producing workstream."
        ),
    )
    node_id: str = Field(
        min_length=1,
        description=(
            "Stable identifier of the emitting node, e.g. 'node-rtl-01'. "
            "Matches NodeConfig.node_id. Used by fusion to associate the "
            "bearing with a node position and to de-duplicate across bearers."
        ),
    )
    t_unix_ns: int = Field(
        gt=0,
        description=(
            "Acquisition timestamp, integer nanoseconds since Unix epoch UTC. "
            "Fusion batches reports into short windows (~100 ms) by this value; "
            "node clocks are NTP-disciplined, which is sufficient for AoA "
            "cross-fixing (unlike TDOA, AoA does not need ns-level sync)."
        ),
    )
    node_position: GeodeticPosition = Field(
        description=(
            "Where the node was when it took this bearing. Carried with every "
            "report (not looked up separately) so a moving/repositioned node "
            "is handled correctly and so fusion is stateless w.r.t. node "
            "positions."
        ),
    )
    azimuth_deg: float = Field(
        ge=0.0,
        lt=360.0,
        description=(
            "Estimated azimuth to the emitter. Degrees, true north = 0, "
            "clockwise positive. This is a geographic bearing -- any antenna/"
            "platform heading correction has already been applied at the node."
        ),
    )
    azimuth_sigma_deg: float = Field(
        gt=0.0,
        description=(
            "1-sigma uncertainty of azimuth_deg, degrees. THE weight the "
            "fusion solver uses (inverse-variance). Must be an honest estimate "
            "from the node's own SNR / pseudospectrum sharpness / calibration "
            "quality -- never a hopeful constant. Strictly > 0."
        ),
    )
    method: Capability = Field(
        description=(
            "How this bearing was produced (L1_RSSI or L2_MUSIC). Lets the "
            "fusion solver and the dashboard distinguish coarse from fine "
            "bearings for display and diagnostics, without changing how they "
            "are mathematically combined."
        ),
    )
    snr_db: float | None = Field(
        default=None,
        description=(
            "Signal-to-noise ratio of the emitter at this node, dB above the "
            "node's noise-floor estimate. Optional: a node may not always have "
            "a meaningful estimate. Diagnostic only -- the bearing weight is "
            "azimuth_sigma_deg, not this. None means 'not reported'."
        ),
    )
    emitter_class: EmitterClass | None = Field(
        default=None,
        description=(
            "L3 classifier label for the emitter this bearing refers to, if "
            "the node ran classification. None means the node did not classify "
            "(no L3 capability, or classifier inconclusive). EmitterClass."
            "UNKNOWN means it classified and is genuinely unsure -- a "
            "different statement from None."
        ),
    )
    classification_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Classifier probability for emitter_class, in [0, 1]. None iff "
            "emitter_class is None. This is the quantitative ML confidence; "
            "the coarse ConfidenceLevel band on a FixEvent is derived from "
            "this plus the geometry, by the fusion workstream."
        ),
    )
    raw_pseudospectrum: bytes | None = Field(
        default=None,
        repr=False,
        description=(
            "Optional debug payload: the L2 MUSIC/Capon pseudospectrum as "
            "little-endian float32 log-magnitude samples over [0, 360) degrees "
            "at a fixed angular step documented in INTERFACES.md. Present only "
            "on the Wi-Fi bearer (dropped on bandwidth-limited LoRa). Consumed "
            "by the ops dashboard for the live pseudospectrum tile; fusion "
            "ignores it. None on all L1 reports and on LoRa-borne reports."
        ),
    )


class FixEvent(BaseModel):
    """A fused emitter geolocation: the cross-fix of several BearingReports.

    Produced by the fusion workstream after it batches bearings from multiple
    nodes. Carries not just the estimated position but the full honesty
    payload an RF/EW jury -- and a real operator -- needs: the covariance, the
    95% ellipse, the per-node residuals, and the geometric dilution of
    precision. A bare lat/lon would be a demo; this is the engineering.

    Consumed by the CoT workstream (rendered as a hostile-emitter marker with
    an ellipse polygon in ATAK) and by the ops dashboard.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Contract version; pinned to SCHEMA_VERSION (see BearingReport).",
    )
    fix_id: UUID = Field(
        description=(
            "Unique identifier for this fix. A moving emitter produces a "
            "stream of FixEvents with distinct fix_ids; the CoT layer may use "
            "a stable track UID separately. Lets the dashboard and logs refer "
            "to an individual solution unambiguously."
        ),
    )
    t_unix_ns: int = Field(
        gt=0,
        description=(
            "Representative timestamp of the fix, integer ns since Unix epoch "
            "UTC -- typically the midpoint of the time window of the "
            "contributing BearingReports."
        ),
    )
    position: GeodeticPosition = Field(
        description=(
            "Estimated emitter position, WGS-84. This is the centre of "
            "confidence_ellipse_95. position.sigma_m is a scalar convenience "
            "summary; the ellipse is the authoritative uncertainty."
        ),
    )
    covariance_m2: tuple[float, float, float] = Field(
        description=(
            "The 2x2 position covariance in the local ENU plane, as "
            "(sigma_xx, sigma_xy, sigma_yy) in square metres -- the unique "
            "entries of the symmetric matrix [[xx, xy], [xy, yy]]. This is the "
            "raw solver output; confidence_ellipse_95 is its geometric "
            "rendering. Both are carried so consumers needing the matrix (e.g. "
            "a downstream tracker) are not forced to invert the ellipse."
        ),
    )
    confidence_ellipse_95: EllipseENU = Field(
        description=(
            "The 95% confidence ellipse in the local ENU plane, centred on "
            "position. The human- and ATAK-facing uncertainty. Derived from "
            "covariance_m2 by the fusion workstream (chi-square scaling "
            "documented in INTERFACES.md)."
        ),
    )
    confidence_level: ConfidenceLevel = Field(
        description=(
            "Coarse display band (HIGH/MEDIUM/LOW) derived by fusion from the "
            "ellipse size, the GDOP, the residuals, and -- if present -- "
            "classification confidence. The discretized summary; the "
            "quantitative truth is the ellipse and gdop fields."
        ),
    )
    contributing_nodes: tuple[str, ...] = Field(
        description=(
            "node_ids of the BearingReports that went into this fix, in the "
            "order their residuals appear in `residuals_deg`. Length is the "
            "number of bearings fused -- minimum 2 for any fix."
        ),
    )
    residuals_deg: tuple[float, ...] = Field(
        description=(
            "Per-node angular residual after the fit, degrees: measured "
            "bearing minus the bearing the solved position implies, for each "
            "node in `contributing_nodes` (same order, same length). A node "
            "with a residual many sigma from zero is flagged by the dashboard "
            "as a likely multipath/calibration outlier -- the system "
            "self-diagnoses."
        ),
    )
    gdop: float = Field(
        gt=0.0,
        description=(
            "Geometric Dilution of Precision for this fix's sensor geometry. "
            "Low (~1-3) means the node placement is favourable; high means "
            "near-collinear geometry that stretches the ellipse regardless of "
            "per-bearing quality. Reported so the operator understands *why* "
            "a fix is loose and where to reposition."
        ),
    )
    method: str = Field(
        description=(
            "How the fix was solved. Expected values: 'stansfield' "
            "(closed-form weighted least squares only), 'stansfield+mle' "
            "(Stansfield seed refined by maximum-likelihood Gauss-Newton -- the "
            "default, since Stansfield alone is biased for finite samples), or "
            "'fallback_centroid' (degenerate geometry; the solver declined to "
            "trust the intersection and returned the weighted centroid of the "
            "bearing-line crossings -- always paired with confidence_level "
            "LOW). Kept as a str, not an enum, because the set of solver modes "
            "is the fusion workstream's internal concern and may evolve "
            "without a contract bump."
        ),
    )
    emitter_class: EmitterClass | None = Field(
        default=None,
        description=(
            "Consensus emitter classification across the contributing nodes' "
            "BearingReports, if any classified it. None means no node "
            "classified; UNKNOWN means nodes classified but did not agree or "
            "were individually unsure. Geolocation never depends on this."
        ),
    )


class NodeStatus(BaseModel):
    """A periodic health heartbeat from a node.

    Consumed by the ops dashboard (to show the mesh at a glance) and by the
    fusion workstream (to decide which nodes are live; a node that has not
    sent a status within a staleness window is excluded from new fixes, and
    that exclusion is itself shown on the dashboard -- the 'pull a node's
    antenna mid-demo and watch the ellipse grow' robustness story).

    Deliberately small: it rides the same bearers as ``BearingReport`` and on
    LoRa every byte counts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Contract version; pinned to SCHEMA_VERSION (see BearingReport).",
    )
    node_id: str = Field(
        min_length=1,
        description="Stable node identifier; matches NodeConfig.node_id.",
    )
    t_unix_ns: int = Field(
        gt=0,
        description="Heartbeat timestamp, integer ns since Unix epoch UTC.",
    )
    position: GeodeticPosition = Field(
        description=(
            "The node's current self-reported position. Usually static, but "
            "carried every heartbeat so a repositioned node updates fusion and "
            "the dashboard without a separate mechanism."
        ),
    )
    active_capabilities: tuple[Capability, ...] = Field(
        description=(
            "What this node is *actually running right now* -- the result of "
            "intersecting its declared NodeConfig.capabilities with what the "
            "detected hardware supports. If this is narrower than the operator "
            "expected, that is visible immediately on the dashboard rather "
            "than as a silent mystery."
        ),
    )
    gnss_locked: bool = Field(
        description=(
            "Whether the node currently has a valid GNSS fix. False in a "
            "GNSS-denied/jammed environment -- itself an EW indicator worth "
            "surfacing -- in which case position is the last-known fix."
        ),
    )
    healthy: bool = Field(
        description=(
            "Node-level self-assessment: True iff the SDR is streaming, the "
            "processing pipeline is keeping up, and the bearer is connected. "
            "A one-glance red/green for the dashboard; detail goes in "
            "status_detail."
        ),
    )
    status_detail: str = Field(
        default="",
        description=(
            "Optional short human-readable elaboration, especially when "
            "healthy is False, e.g. 'SDR overflow' or 'LoRa bearer down, Wi-Fi "
            "only'. Empty string when there is nothing to add."
        ),
    )
