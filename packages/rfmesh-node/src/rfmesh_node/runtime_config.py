"""``NodeRuntimeConfig`` -- single-YAML wrapper around the frozen ``NodeConfig``.

ADR-022 (PROPOSED 2026-05-23). Composes -- without modifying -- the frozen
``NodeConfig`` (B1) with the four node-runtime helper configs that ship in
this package:

* ``SweepOverrideConfig`` -- override defaults on ``L1SweepConfig`` (no schema
  change to the dataclass; this just lets YAML drive the same fields).
* ``RendezvousOverrideConfig`` -- override defaults on ``RendezvousConfig``
  (ADR-019). ``peer_node_id`` + ``peer_position`` are required (per ADR-019,
  they are the operator/config trigger; no node-to-node discovery channel).
* ``CommandEndpointConfig`` -- backend WS URL for the comms-mode command
  channel (ADR-021 §"Transport"). Required for soldier-facing operation;
  optional for sweep-only bench runs.
* ``MotionOverrideConfig`` -- override defaults on
  ``servo_motion.MotionConfig`` for the trapezoidal ramp.

Every sub-model is ``frozen=True, extra="forbid"`` -- a typo'd YAML key is a
loud error at load time (B3, the data-layer half).

Why not in ``rfmesh-contracts``? Two reasons. (1) These are node-runtime
helpers, not cross-workstream data products -- the contracts package is
reserved for messages and configs that *cross* a workstream boundary.
(2) Embedding them in contracts would force a ``SCHEMA_VERSION`` bump on
every node-runtime tweak; this wrapper can evolve without churning every
other workstream's mypy gate (ADR-012 tripwire).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rfmesh_contracts import CommsConfig, GeodeticPosition, NodeConfig

from .comms import CommsLinkConfig, PeerEntry
from .l1_sweep import L1SweepConfig
from .rendezvous import RendezvousConfig
from .servo_motion import MotionConfig


class SweepOverrideConfig(BaseModel):
    """YAML override of ``L1SweepConfig`` defaults.

    Defaults mirror the dataclass exactly; a YAML stanza with only the keys
    the operator wants to change leaves the rest at their defaults. The
    arc must remain wider than the antenna HPBW (50 deg for the ATK-10) or
    the L1 estimator's prominence gate measures off-axis floor inside the
    main lobe -- enforced downstream by ``L1SweepLoop`` (raises on < 7 angles).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    axis: int = Field(default=0, ge=0, le=255)
    min_deg: float = Field(default=-90.0)
    max_deg: float = Field(default=90.0)
    step_deg: float = Field(default=2.0, gt=0.0)
    settle_s: float = Field(default=0.20, ge=0.0)
    dwell_samples: int = Field(default=1024, gt=0)
    inter_sweep_s: float = Field(default=1.0, ge=0.0)
    peak_prominence_db_min: float = Field(default=6.0, gt=0.0)
    hold_on_peak: bool = Field(
        default=False,
        description=(
            "MVP scan-and-hold mode. After the first sweep produces a "
            "BearingReport the servo points at the peak heading and STAYS "
            "there until shutdown; subsequent iterations re-emit the same "
            "bearing every inter_sweep_s with a refreshed timestamp so the "
            "fusion ellipse stays fresh on the operator UI."
        ),
    )

    @model_validator(mode="after")
    def _arc_order(self) -> SweepOverrideConfig:
        if self.min_deg >= self.max_deg:
            msg = (
                f"SweepOverrideConfig: min_deg ({self.min_deg}) must be < max_deg ({self.max_deg})."
            )
            raise ValueError(msg)
        return self

    def to_dataclass(self) -> L1SweepConfig:
        """Materialise to the frozen ``L1SweepConfig`` dataclass."""
        return L1SweepConfig(
            axis=self.axis,
            min_deg=self.min_deg,
            max_deg=self.max_deg,
            step_deg=self.step_deg,
            settle_s=self.settle_s,
            dwell_samples=self.dwell_samples,
            inter_sweep_s=self.inter_sweep_s,
            peak_prominence_db_min=self.peak_prominence_db_min,
            hold_on_peak=self.hold_on_peak,
        )


class _PeerPosition(BaseModel):
    """YAML shape for ``RendezvousConfig.peer_position``.

    Same fields as ``GeodeticPosition`` (lat, lon, hae, sigma) but defined
    here so the YAML schema is ``extra="forbid"`` even on this nested
    object. Materialises to the frozen contracts type.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lat_deg: float = Field(ge=-90.0, le=90.0)
    lon_deg: float = Field(gt=-180.0, le=180.0)
    hae_m: float = Field(default=0.0)
    sigma_m: float = Field(default=10.0, ge=0.0)

    def to_contract(self) -> GeodeticPosition:
        return GeodeticPosition(
            lat_deg=self.lat_deg,
            lon_deg=self.lon_deg,
            hae_m=self.hae_m,
            sigma_m=self.sigma_m,
        )


class RendezvousOverrideConfig(BaseModel):
    """YAML override of ``RendezvousConfig`` defaults (ADR-019).

    ``peer_node_id`` + ``peer`` are required (no defaults) -- they are the
    operator-supplied trigger that enables rendezvous at all (per ADR-019,
    there is no node-to-node discovery channel). Other fields override the
    dataclass defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    peer_node_id: str = Field(min_length=1)
    peer: _PeerPosition
    axis: int = Field(default=0, ge=0, le=255)
    refine_half_arc_deg: float = Field(default=20.0, gt=0.0)
    refine_step_deg: float = Field(default=2.0, gt=0.0)
    settle_s: float = Field(default=0.20, ge=0.0)
    dwell_samples: int = Field(default=1024, gt=0)
    peak_prominence_db_min: float = Field(default=6.0, gt=0.0)
    min_servo_deg: float = Field(default=-90.0)
    max_servo_deg: float = Field(default=90.0)
    link_hold_s: float = Field(default=30.0, gt=0.0)
    retry_pause_s: float = Field(default=2.0, ge=0.0)
    escalation_half_arcs_deg: tuple[float, ...] = (20.0, 45.0, 90.0)

    @model_validator(mode="after")
    def _arc_order(self) -> RendezvousOverrideConfig:
        if self.min_servo_deg >= self.max_servo_deg:
            msg = (
                f"RendezvousOverrideConfig: min_servo_deg ({self.min_servo_deg}) "
                f"must be < max_servo_deg ({self.max_servo_deg})."
            )
            raise ValueError(msg)
        if not self.escalation_half_arcs_deg:
            msg = "RendezvousOverrideConfig.escalation_half_arcs_deg must be non-empty."
            raise ValueError(msg)
        return self

    def to_dataclass(self) -> RendezvousConfig:
        """Materialise to the frozen ``RendezvousConfig`` dataclass."""
        return RendezvousConfig(
            peer_node_id=self.peer_node_id,
            peer_position=self.peer.to_contract(),
            axis=self.axis,
            refine_half_arc_deg=self.refine_half_arc_deg,
            refine_step_deg=self.refine_step_deg,
            settle_s=self.settle_s,
            dwell_samples=self.dwell_samples,
            peak_prominence_db_min=self.peak_prominence_db_min,
            min_servo_deg=self.min_servo_deg,
            max_servo_deg=self.max_servo_deg,
            link_hold_s=self.link_hold_s,
            retry_pause_s=self.retry_pause_s,
            escalation_half_arcs=tuple(self.escalation_half_arcs_deg),
        )


class CommandEndpointConfig(BaseModel):
    """Backend WS URL for the comms-mode command channel (ADR-021).

    A node-runtime sub-config (not in ``rfmesh-contracts`` per ADR-018 -- UI
    coordination is server-vs-node infrastructure, not a cross-workstream
    data product).

    When ``enabled=False`` (default) the node does NOT phone home over WS;
    it ships bearings via the existing ``HttpBearer`` and is invisible to
    ``link.html``. Soldier-facing deployments set ``enabled=True`` and
    point ``backend_ws_url`` at the backend (``ws://10.0.0.1:8000``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    backend_ws_url: str | None = None

    @model_validator(mode="after")
    def _url_required_when_enabled(self) -> CommandEndpointConfig:
        if self.enabled and not self.backend_ws_url:
            msg = (
                "CommandEndpointConfig.enabled=True requires backend_ws_url "
                '(e.g. "ws://10.0.0.1:8000"); disable it for sweep-only bench runs.'
            )
            raise ValueError(msg)
        if self.backend_ws_url is not None:
            scheme_prefix = self.backend_ws_url[:5].lower()
            if not (
                self.backend_ws_url.startswith("ws://") or self.backend_ws_url.startswith("wss://")
            ):
                msg = (
                    f"CommandEndpointConfig.backend_ws_url must use ws:// or wss:// "
                    f"(got scheme prefix {scheme_prefix!r})."
                )
                raise ValueError(msg)
        return self


class MotionOverrideConfig(BaseModel):
    """YAML override of ``servo_motion.MotionConfig`` defaults.

    Empty stanza = library defaults (conservative for an MG996R clone driving
    an ATK-10 Yagi on a tripod mast). Operators with sturdier mast or larger
    antenna slow them further (lower ``max_vel_dps``, longer ``accel_time_s``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_vel_dps: float = Field(default=25.0, gt=0.0)
    accel_time_s: float = Field(default=0.40, ge=0.0)
    motion_step_deg: float = Field(default=1.0, gt=0.0)
    fast_threshold_deg: float = Field(default=10.0, ge=0.0)
    fast_settle_s: float = Field(default=0.10, ge=0.0)
    invert_direction: bool = False

    def to_dataclass(self) -> MotionConfig:
        return MotionConfig(
            max_vel_dps=self.max_vel_dps,
            accel_time_s=self.accel_time_s,
            motion_step_deg=self.motion_step_deg,
            fast_threshold_deg=self.fast_threshold_deg,
            fast_settle_s=self.fast_settle_s,
            invert_direction=self.invert_direction,
        )


class CommsOverrideConfig(BaseModel):
    """YAML stanza enabling DSSS directional-comms mode (ADR-025 Iter 4).

    Wraps two distinct things into one operator-facing block:

    * Physical-layer parameters (``carrier``, ``chip_rate``,
      ``spreading_factor``, LFSR, TDD timings, max payload) -- these
      materialise into the frozen ``rfmesh_contracts.CommsConfig``.
    * Per-link policy (``peer``, ``link_role``, settle, hold,
      initial message) -- these materialise into the node-layer
      ``CommsLinkConfig``.

    Presence of a ``comms:`` block enables COMMS mode. Mutually
    exclusive with ``rendezvous:`` (mutex enforced by
    ``NodeRuntimeConfig._coherence``) -- one Yagi + one SDR cannot
    run both DF rendezvous and DSSS comms simultaneously in v1.3.0
    (ADR-025 Decision B).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Physical layer (mirrors CommsConfig defaults; YAML can override any).
    carrier_freq_hz: float = Field(gt=0.0)
    chip_rate_hz: float = Field(gt=0.0)
    spreading_factor: int = Field(default=1023, ge=3)
    lfsr_taps: tuple[int, ...] = (10, 3)
    lfsr_seed: int = Field(default=1, gt=0)
    tdd_slot_ms: float = Field(default=200.0, gt=0.0)
    tdd_guard_ms: float = Field(default=30.0, ge=0.0)
    frame_payload_max_bytes: int = Field(default=64, gt=0)

    # Per-link policy (mirrors CommsLinkConfig defaults).
    peer_node_id: str = Field(min_length=1)
    peer: _PeerPosition
    peer_boresight_heading_deg: float | None = None
    link_role: str = Field(default="tx_first")
    refine_half_arc_deg: float = Field(default=20.0, gt=0.0)
    settle_s: float = Field(default=0.20, ge=0.0)
    min_servo_deg: float = Field(default=-90.0)
    max_servo_deg: float = Field(default=90.0)
    link_hold_s: float = Field(default=30.0, gt=0.0)
    initial_outbox_message_text: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> CommsOverrideConfig:
        if self.link_role not in ("tx_first", "rx_first"):
            msg = f"link_role must be 'tx_first' or 'rx_first' (got {self.link_role!r})."
            raise ValueError(msg)
        if self.min_servo_deg >= self.max_servo_deg:
            msg = (
                f"CommsOverrideConfig: min_servo_deg ({self.min_servo_deg}) "
                f"must be < max_servo_deg ({self.max_servo_deg})."
            )
            raise ValueError(msg)
        if not self.lfsr_taps:
            msg = "lfsr_taps must be non-empty."
            raise ValueError(msg)
        return self

    def to_comms_config(self) -> CommsConfig:
        """Materialise the frozen-contract physical-layer config."""
        return CommsConfig(
            carrier_freq_hz=self.carrier_freq_hz,
            chip_rate_hz=self.chip_rate_hz,
            spreading_factor=self.spreading_factor,
            lfsr_taps=tuple(self.lfsr_taps),
            lfsr_seed=self.lfsr_seed,
            tdd_slot_ms=self.tdd_slot_ms,
            tdd_guard_ms=self.tdd_guard_ms,
            frame_payload_max_bytes=self.frame_payload_max_bytes,
        )

    def to_link_config(self) -> CommsLinkConfig:
        """Materialise the node-layer link policy dataclass."""
        peer_entry = PeerEntry(
            node_id=self.peer_node_id,
            position=self.peer.to_contract(),
            boresight_heading_deg=self.peer_boresight_heading_deg,
        )
        initial = (
            self.initial_outbox_message_text.encode("utf-8")
            if self.initial_outbox_message_text is not None
            else None
        )
        # link_role is validated above to be one of the two literals.
        role: str = self.link_role  # narrowed at validator
        link_role_lit: object = role
        return CommsLinkConfig(
            peer=peer_entry,
            link_role=link_role_lit,  # type: ignore[arg-type]
            refine_half_arc_deg=self.refine_half_arc_deg,
            settle_s=self.settle_s,
            min_servo_deg=self.min_servo_deg,
            max_servo_deg=self.max_servo_deg,
            link_hold_s=self.link_hold_s,
            initial_outbox_message=initial,
        )


class NodeRuntimeConfig(BaseModel):
    """Single-YAML root for one running node (ADR-022).

    Composes the frozen ``NodeConfig`` (`node:` block, verbatim contract) with
    the four node-runtime helper sub-configs. The YAML root looks like:

    ```yaml
    node:                  # NodeConfig (frozen contract, do not extend here)
      schema_version: "1.3.0"
      node_id: node-rtl-01
      position: {lat_deg: ..., lon_deg: ...}
      heading_deg: 142.0
      sdr: {driver: rtlsdr, sample_rate_hz: 2.048e6, center_freq_hz: 868.0e6, gain_db: 30.0}
      capabilities: [l1_rssi]
      bearer: {kind: wifi, heartbeat_interval_s: 2.0}
      fusion_endpoint: "http://10.0.0.1:8000/bearings"
    servo_port: /dev/ttyACM0       # null disables the servo (heartbeat-only node)
    sweep:                         # L1SweepConfig overrides; empty = defaults
      step_deg: 2.0
    rendezvous:                    # null disables rendezvous; present = ADR-019 enabled
      peer_node_id: node-rtl-02
      peer: {lat_deg: 50.34, lon_deg: 5.01}
    command_endpoint:              # null/disabled = HTTP-only bearer; no UI control
      enabled: true
      backend_ws_url: ws://10.0.0.1:8000
    motion:                        # servo_motion.MotionConfig overrides; null = defaults
      max_vel_dps: 25.0
    ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node: NodeConfig
    servo_port: str | None = None
    sweep: SweepOverrideConfig = Field(default_factory=SweepOverrideConfig)
    rendezvous: RendezvousOverrideConfig | None = None
    comms: CommsOverrideConfig | None = None
    command_endpoint: CommandEndpointConfig = Field(default_factory=CommandEndpointConfig)
    motion: MotionOverrideConfig = Field(default_factory=MotionOverrideConfig)

    @model_validator(mode="after")
    def _coherence(self) -> NodeRuntimeConfig:
        # ADR-025 Decision B: COMMS mode and DF rendezvous share the
        # one Yagi + one SDR; running both concurrently on the same
        # node is undefined. The two YAML blocks are mutually
        # exclusive at config-load time (B3 -- loud at startup,
        # not silently arbitrated at runtime).
        if self.comms is not None and self.rendezvous is not None:
            msg = (
                "NodeRuntimeConfig: 'comms' and 'rendezvous' are mutually "
                "exclusive in v1.3.0 (ADR-025 Decision B). One Yagi + one "
                "SDR cannot run both directional DSSS comms and DF "
                "rendezvous concurrently; pick one block and remove the "
                "other."
            )
            raise ValueError(msg)
        # A rendezvous block needs a servo to drive; refuse the combination
        # at load time rather than during ``Node.run`` (B3, loud-not-silent).
        if self.rendezvous is not None and self.servo_port is None:
            msg = (
                "NodeRuntimeConfig: rendezvous requires servo_port to be set "
                "(rendezvous drives the servo). Either set servo_port or drop "
                "the rendezvous block."
            )
            raise ValueError(msg)
        # Same constraint for comms: the directional link needs a
        # servo to point the Yagi at the peer (Iter 4 acquire step).
        if self.comms is not None and self.servo_port is None:
            msg = (
                "NodeRuntimeConfig: comms requires servo_port to be set "
                "(comms loop points the Yagi at the peer). Either set "
                "servo_port or drop the comms block."
            )
            raise ValueError(msg)
        # Rendezvous + L1 sweep must agree on the servo arc -- L1SweepLoop and
        # RendezvousLoop both clamp to their respective arcs, but the YAML
        # makes it obvious if the operator typoed one of them. Cross-check
        # that the rendezvous arc is contained in the sweep arc; if not,
        # SCANNER refine would point outside the L1 sweep's calibrated arc.
        if self.rendezvous is not None:
            rv = self.rendezvous
            sw = self.sweep
            if rv.min_servo_deg < sw.min_deg or rv.max_servo_deg > sw.max_deg:
                msg = (
                    f"NodeRuntimeConfig: rendezvous arc "
                    f"[{rv.min_servo_deg:.0f}, {rv.max_servo_deg:.0f}] is wider "
                    f"than the L1 sweep arc [{sw.min_deg:.0f}, {sw.max_deg:.0f}]. "
                    "Narrow the rendezvous arc or widen the sweep arc."
                )
                raise ValueError(msg)
        return self

    def sweep_dataclass(self) -> L1SweepConfig:
        """Build the frozen ``L1SweepConfig`` honouring axis from `sweep`."""
        return self.sweep.to_dataclass()

    def rendezvous_dataclass(self) -> RendezvousConfig | None:
        """Build the frozen ``RendezvousConfig``, or ``None`` if disabled."""
        if self.rendezvous is None:
            return None
        return self.rendezvous.to_dataclass()

    def motion_dataclass(self) -> MotionConfig:
        """Build the frozen ``MotionConfig`` for the trapezoidal ramp."""
        return self.motion.to_dataclass()

    def comms_link_dataclass(self) -> CommsLinkConfig | None:
        """Build the node-layer ``CommsLinkConfig``, or ``None`` if comms disabled."""
        if self.comms is None:
            return None
        return self.comms.to_link_config()

    def comms_contract(self) -> CommsConfig | None:
        """Build the frozen-contract ``CommsConfig``, or ``None`` if comms disabled."""
        if self.comms is None:
            return None
        return self.comms.to_comms_config()


__all__ = [
    "CommandEndpointConfig",
    "CommsOverrideConfig",
    "MotionOverrideConfig",
    "NodeRuntimeConfig",
    "RendezvousOverrideConfig",
    "SweepOverrideConfig",
]
