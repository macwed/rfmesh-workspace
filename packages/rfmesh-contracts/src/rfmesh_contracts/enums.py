"""Enumerations shared across every rfmesh workstream.

Every enum here is a ``str`` enum so that it serializes to a human-readable
token in YAML configs, JSON-over-the-wire messages, and CoT XML without any
custom encoder. The string values are part of the frozen contract: renaming a
value is a MAJOR version bump (see ``version.py``).

Why enums and not bare strings: a bare string ``"l2_music"`` typo'd as
``"l2_musci"`` in one workstream is a runtime mystery. ``Capability.L2_MUSIC``
typo'd is an immediate ``AttributeError`` at import time in that workstream's
own test run, before any integration. Cheap, local, early failure.
"""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    """What a single mesh node can do with the hardware it has.

    A node *declares* a set of capabilities in its ``NodeConfig`` (see
    ``config.py``). At startup the node runtime intersects the declared set
    with what the detected SDR + antenna array can physically support. A
    mismatch is a *fatal* error with a clear message -- never a silent
    downgrade. (See ``AGENTS.md`` -> Invariant 4: "No silent fallbacks.")

    The three capability layers, as used in the pitch and the architecture:

    * ``L1_RSSI`` -- amplitude-comparison direction finding. A directional
      antenna (Yagi) on a servo sweeps in azimuth; the RSSI peak gives a
      bearing. Works on *any* single-channel SDR. Coarse: ~5-15 deg per node.
    * ``L2_MUSIC`` -- phase-coherent subspace direction finding (MUSIC
      pseudospectrum) on a 2+ element antenna array. Requires a phase-coherent
      multi-channel SDR (bladeRF 2.0 micro, ADALM-Pluto+). Fine: ~1-3 deg.
    * ``L2_CAPON`` -- phase-coherent Capon (a.k.a. MVDR-spectrum) direction
      finding on the same array. Same R as MUSIC; different DoA estimator
      (peak of ``1 / (a^H R^-1 a)`` instead of MUSIC noise-subspace
      projector). Robust to small SNR / few-snapshot regimes where MUSIC's
      eigendecomposition struggles. Added in SCHEMA_VERSION 1.1.0
      (ADR-008). 1-3 deg under benign conditions.
    * ``L2_MVDR_NULL`` -- the **null-steering** dual-use sibling of
      ``L2_CAPON`` (and ``L2_MUSIC``). The *same* sample covariance R is
      inverted via the MVDR distortionless-response weight formula
      ``w = R^-1 a / (a^H R^-1 a)`` (where ``a`` is the look-direction
      steering vector) to synthesise a spatial null toward any strong
      off-look emitter present in R -- typically a co-channel jammer.
      Protects the project's own L2 coherent DF channel from
      desensitisation by the jammer while the system continues to
      produce bearings on it (**anti-desense, not ECM** -- this distinction
      matters for the BoTH3 pitch). Does NOT emit a ``BearingReport``;
      it is a receive-weight synthesiser. Reserved in SCHEMA_VERSION 1.1.0
      (ADR-008); the implementation lives in
      ``rfmesh_dsp.l2_null_steering`` (WS-B-007). A node declaring
      ``L2_MVDR_NULL`` in ``NodeConfig.capabilities`` advertises the
      receive-side null-steering operation as an operator action; no
      streamed wire-format product is emitted in v1.1.0.
    * ``L3_CLASSIFY`` -- edge ML emitter classification (STFT spectrogram into
      a CNN/ResNet). Labels the emitter (ELRS, Crossfire, GSM jammer, ...).
      Needs a compute node (Raspberry Pi class) but is SDR-agnostic.
    * ``L1_REFUSED_PROMINENCE`` -- a *capability state*, not a bearing method.
      Added in SCHEMA_VERSION 1.2.0 (ADR-013). A ``BearingReport`` carrying
      ``method = L1_REFUSED_PROMINENCE`` is the wire-level surface for an L1
      refusal: the L1 amplitude-sweep estimator inspected a sweep and
      declined to emit a bearing (prominence-gate failure, saddle, vertex
      out of window, singular covariance, non-finite variance,
      under-populated sweep). The free-form cause travels on
      ``BearingReport.refusal_reason``. The fuser SKIPS these reports
      (they do not contribute to a fix); they exist so a remote dashboard
      can render the refusal as a structured event instead of seeing
      nothing arrive from that node for one batch window
      (Mast A in ``docs/phase-c-report/findings.md`` is the canonical
      example). Producers populate the bearing-direction fields with
      sentinels: ``azimuth_deg=0.0`` and
      ``azimuth_sigma_deg=180.0`` (infinite-uncertainty equivalent),
      because the contract requires both to be present. Consumers MUST
      branch on ``method`` first and not interpret those sentinels as a
      real bearing.
    * ``COMMS_DSSS`` -- the node participates in the DSSS directional mesh
      (ADR-025). Requires *both* an RX path and a TX path on the
      configured SDR (HackRF One, ADALM-Pluto+, BladeRF 2.0 micro);
      RTL-SDR V4 is RX-only and a node declaring ``COMMS_DSSS`` on
      RTL-SDR hardware is a fatal startup error (B3). Mutually
      exclusive with the DF capabilities (``L1_RSSI``, ``L2_MUSIC``,
      ``L2_CAPON``, ``L2_MVDR_NULL``) in v1.3.0 -- a node runs DF mode
      OR comms mode, never both concurrently on one SDR/Yagi.
      ``L3_CLASSIFY`` may coexist with ``COMMS_DSSS`` (SDR-agnostic
      classification on tapped IQ). Added in SCHEMA_VERSION 1.3.0.
    """

    L1_RSSI = "l1_rssi"
    L2_MUSIC = "l2_music"
    L2_CAPON = "l2_capon"
    L2_MVDR_NULL = "l2_mvdr_null"
    L3_CLASSIFY = "l3_classify"
    L1_REFUSED_PROMINENCE = "l1_refused_prominence"
    COMMS_DSSS = "comms_dsss"


class BearingPriorKind(StrEnum):
    """The epistemic status of a ``BearingReport``'s azimuth prior (ADR-026).

    Orthogonal to ``Capability`` (the estimator-type axis). A peer-acquired
    bearing produced by the SAME L1_RSSI estimator path that produces emitter
    bearings carries ``prior_kind = PEER_LINK``; an incidental secondary peak
    from the same sweep carries ``prior_kind = FLAT``.

    Added in SCHEMA_VERSION 1.4.0. A legacy producer (pre-1.4.0) that omits
    the field entirely is treated by consumers as ``FLAT`` -- that is the
    documented backwards-compatibility contract.
    """

    #: No prior -- the bearing is a measurement of an unknown emitter.
    FLAT = "flat"
    #: Bayesian prior from a known peer link (surveyed position + prior
    #: comms). Producer MUST also populate ``BearingReport.prior_mean_deg``
    #: and ``BearingReport.prior_sigma_deg``; the validator enforces this
    #: coherence. Fusion filters these reports out of emitter ``FixEvent``
    #: computation (ADR-026 Q3 per-peak filter).
    PEER_LINK = "peer_link"


class EmitterClass(StrEnum):
    """Emitter type label produced by the L3 classifier.

    This is the *open, extensible threat library* that is the project's
    competitive moat: commercial gear (e.g. RfPatrol Mk2) ships a closed
    library; military systems (e.g. Bukovel-AD) keep theirs classified. Adding
    a new member here is a MINOR version bump and must be paired with a threat
    profile module under ``rfmesh-ml/threats/`` plus documentation in
    ``docs/threat-library.md``.

    ``UNKNOWN`` is mandatory and load-bearing: a classifier that has *not* seen
    a signature before must say so, never guess. Downstream consumers (fusion,
    CoT) treat ``UNKNOWN`` as "emitter present, type unconfirmed" and still
    geolocate it -- detection does not depend on classification.

    Members marked "(stub)" have a profile module that is a documented
    placeholder pending real IQ capture; they exist in the contract so the
    pipeline shape is stable, but the classifier will not emit them until the
    profile is trained. See ``docs/threat-library.md`` for capture status.
    """

    UNKNOWN = "unknown"
    #: ExpressLRS R/C control link (868/915 MHz or 2.4 GHz, LoRa-based FHSS).
    ELRS = "elrs"
    #: TBS Crossfire R/C control link (868/915 MHz, long-range FHSS).
    CROSSFIRE = "crossfire"
    #: GSM-band handset emission -- the analog of an IED command-detonation
    #: phone. Distinctive in *context* (a lone uplink burst from a static,
    #: non-infrastructure location), not in waveform.
    GSM_JAMMER = "gsm_jammer"
    #: Pole-21 GNSS jamming complex (stub -- profile pending real capture).
    POLE21 = "pole21"
    #: Volnorez vehicle-mounted counter-FPV jammer (stub -- profile pending).
    VOLNOREZ = "volnorez"
    #: DJI DroneID / OcuSync downlink beacon (Mavic-class platforms).
    DRONEID = "droneid"


class ConfidenceLevel(StrEnum):
    """Coarse, human-facing confidence band for a fix or a classification.

    This is *not* the quantitative uncertainty -- that lives in
    ``FixEvent.covariance_m2`` / ``confidence_ellipse_95`` for geometry and in
    ``BearingReport.classification_confidence`` (a 0..1 float) for the ML
    label. ``ConfidenceLevel`` is the discretized version for display: an ATAK
    marker colour, a one-word annotation on the ops dashboard. Keeping the
    quantitative and the qualitative separate avoids the trap of an operator
    reading "0.62" as if it were calibrated probability.

    Mapping from quantitative evidence to band is the *fusion* workstream's
    responsibility and is documented in ``INTERFACES.md`` under ``FixEvent``.
    """

    #: Geometry is strong (low GDOP, tight ellipse, residuals consistent) or
    #: classification probability is high. Safe to action.
    HIGH = "high"
    #: Usable but caveated -- moderate GDOP, or a single redundant bearing, or
    #: a mid-probability classification. Cue further collection.
    MEDIUM = "medium"
    #: Weak -- near-collinear geometry, an ellipse larger than the operational
    #: tolerance, or a low-probability label. Report, but do not action alone.
    LOW = "low"


class ArrayGeometry(StrEnum):
    """Physical layout of a phase-coherent antenna array on an L2 node.

    The geometry determines the steering-vector / array-manifold model that
    the L2 DSP code uses. It is declared per-node in ``ArrayConfig`` (see
    ``config.py``); the DSP workstream keys its steering-vector construction
    off this value and never hard-codes a layout.

    * ``ULA`` -- uniform linear array. Two or more elements, equal spacing
      along a line. Simplest manifold. Inherent front/back (mirror) ambiguity
      about the array axis -- resolved by an extra element, a ground-plane /
      reflector, a coarse L1 bearing from the same node, or platform motion.
    * ``UCA`` -- uniform circular array. Elements equally spaced on a circle.
      No front/back ambiguity, 360 deg unambiguous coverage; needs >=3
      elements. This is the KrakenSDR-style layout.
    * ``CUSTOM`` -- arbitrary element coordinates supplied explicitly in
      ``ArrayConfig.element_positions_m``. Escape hatch for whatever hardware
      the partner pool actually yields on-site.
    """

    ULA = "ula"
    UCA = "uca"
    CUSTOM = "custom"


class BearerKind(StrEnum):
    """Transport used to carry messages between a node and the fusion server.

    Absorbed entirely inside the node-runtime workstream's transport layer;
    DSP and fusion code never see this. Listed in the contract only because
    ``NodeConfig`` must declare it.

    * ``WIFI`` -- UDP + msgpack over Wi-Fi / Ethernet. Primary: high bandwidth,
      low latency, carries optional debug payloads (raw pseudospectra).
    * ``LORA`` -- compressed bearing reports over a LoRa link. Fallback for
      EW-contested or long-baseline conditions: bandwidth is tiny, so only the
      essential ``BearingReport`` fields are sent, debug payloads dropped.
    * ``BOTH`` -- run both; fusion de-duplicates by ``(node_id, t_unix_ns)``.
      Wi-Fi preferred when healthy, LoRa as hot standby.
    """

    WIFI = "wifi"
    LORA = "lora"
    BOTH = "both"
