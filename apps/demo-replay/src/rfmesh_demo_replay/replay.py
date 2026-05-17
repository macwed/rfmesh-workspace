"""``ReplayOrchestrator`` -- in-process multi-node demo orchestrator.

Brings up the rfmesh demo stack inside *one* Python process and walks
through the beats of a scenario:

* one ``SyntheticReceiver`` per node (synthesise mode for v1.0);
* one or more ``BearingEstimator`` per node, driven directly by the
  orchestrator's beat loop (the production estimator-loop wiring in
  ``rfmesh-node`` is intentionally minimal at v1.0 -- see
  ``packages/rfmesh-node/src/rfmesh_node/node.py`` module docstring);
* one shared ``FusionService`` from ``rfmesh-node`` consuming the
  bearings via its ``push_for_test`` inbox;
* one ``DashboardPubSub`` + optional in-process ``Dashboard`` (when
  ``--headless`` is not set);
* one optional ``PyTAKCotPublisher`` (when ``--no-cot`` is not set
  AND the scenario's ``fusion.cot_url`` is set).

WHY IN-PROCESS, NOT MULTI-PROCESS
---------------------------------
Architect §2.4 phrases the orchestrator as "spawning N node
processes". For v1.0 BoTH3 (2-5 nodes on one operator laptop), an
in-process orchestrator is:

* faster to build and debug (no subprocess management, no signal
  handling, no log multiplexing);
* lower bug surface (one event loop, one stop signal);
* still demonstrates the same behaviour on stage -- the jury sees
  the dashboard, not the process tree.

The migration path is one new ``multiprocessing.Process``-per-node
helper layered on top -- the orchestrator's design (one
``_NodeContext`` per node, all driving the same ``FusionService``)
maps directly onto the multi-process shape when needed. This is
documented at the boundary so a future ticket can lift it without
re-architecting.

WHY DRIVE ESTIMATORS DIRECTLY (NOT VIA ``Node.run``)
----------------------------------------------------
The v1.0 ``Node`` class is a lifecycle / bearer container -- its
estimator loop is a "future WS-CD ticket layered on top". For
demo-replay we drive the simulator + estimators directly:
``SyntheticReceiver.set_antenna_heading`` + ``read(N)`` per
heading -> ``observe`` (L1) or single ``read_coherent(N)`` + ``estimate``
(L2) -> ``BearingReport`` -> ``FusionService.push_for_test``. This
mirrors what ``Node`` will eventually do once that ticket lands.
The downside is one extra integration point; the upside is the
demo runs *today* on the simulator + estimators that already exist.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from rfmesh_contracts import (
    ArrayConfig,
    ArrayGeometry,
    BearingReport,
    Capability,
    FixEvent,
)
from rfmesh_dsp.l1 import L1AmplitudeSweepEstimator  # type: ignore[import-untyped]
from rfmesh_dsp.l2_music import L2MusicEstimator  # type: ignore[import-untyped]
from rfmesh_fusion import StansfieldMLEFuser  # type: ignore[import-untyped]
from rfmesh_node.dashboard_pubsub import DashboardPubSub, InProcessSubscriber
from rfmesh_node.fusion_service import FusionService
from rfmesh_sdr.simulator import (  # type: ignore[import-untyped]
    AntennaPattern,
    ArraySpec,
    ChannelModel,
    CompositeChannel,
    FreeSpaceChannel,
    LogNormalShadowing,
    MultipathFIRChannel,
    SimulationScenario,
    SyntheticReceiver,
    TwoRayGroundChannel,
)
from rfmesh_sdr.simulator import (
    EmitterSpec as SimEmitterSpec,
)

from .scenario import (
    ChannelModelSpec,
    DemoBeatSpec,
    NodeReplaySpec,
    Scenario,
)

if TYPE_CHECKING:
    from rfmesh_contracts import CotPublisher, NodeConfig

_LOG = logging.getLogger(__name__)


# Sweep parameters for the orchestrator-driven L1 path. The L1
# estimator's ``sweep_dwell_samples`` is a constructor argument; we
# pick a value that's coherent with what ``build_estimators`` defaults
# to (1024) so the estimator's guard does not trip.
# Sweep parameters chosen so the L1 estimator sees a clear parabola:
#   half_arc = 45 deg, step = 5 deg -> 19 samples across [-45, +45] from
#   boresight. With a 50 deg HPBW pattern, the wings (>= +/-25 deg) lie
#   below the -3 dB point so the median noise-floor estimator catches
#   the off-axis floor. Narrower sweeps cause the median floor to ride
#   too close to the peak and trip the prominence guard.
_L1_SWEEP_DWELL_SAMPLES: int = 1024
_L1_SWEEP_HALF_ARC_DEG: float = 60.0
_L1_SWEEP_STEP_DEG: float = 5.0
# Coherent capture size per L2 estimate -- a few thousand samples
# converges MUSIC reliably without making the orchestrator slow.
_L2_COHERENT_SAMPLES: int = 4096


# ---------------------------------------------------------------------------
# Per-node context inside the orchestrator
# ---------------------------------------------------------------------------


@dataclass
class _NodeContext:
    """Everything one node needs to participate in the orchestrator.

    Private to this module; the orchestrator's public surface is the
    ``ReplayOrchestrator`` class + ``run``.
    """

    spec: NodeReplaySpec
    node_config: NodeConfig
    receiver: SyntheticReceiver
    l1: L1AmplitudeSweepEstimator | None = None
    l2_music: L2MusicEstimator | None = None
    # Beats during which this node is "online".
    active_beats: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class ReplayOrchestrator:
    """In-process orchestrator for the multi-node demo.

    Construct with a loaded ``Scenario``; ``run`` walks the beats,
    drives the per-node estimators, pumps bearings into a shared
    ``FusionService``, and lets a ``DashboardPubSub`` fan-out fan-in
    to the dashboard.
    """

    def __init__(
        self,
        scenario: Scenario,
        *,
        pessimism_factor: float = 1.0,
        enable_cot: bool = False,
        enable_dashboard: bool = True,
        fix_sink: asyncio.Queue[FixEvent] | None = None,
        cot_publisher: CotPublisher | None = None,
    ) -> None:
        """Wire the orchestrator against a scenario.

        Args:
            scenario: A loaded ``Scenario``. The orchestrator does not
                load YAML -- the loader's caller does.
            pessimism_factor: Scalar applied to the simulator's noise
                floor (in dB-space, ``noise_floor_dbfs * factor``) to
                inflate per-bearing sigma for the Phase-C overlay
                demos. 1.0 means "honest", 1.5 / 2.0 are the policy
                pessimism overlays per ``INHERITED_CONTEXT.md`` §3.1.1.
            enable_cot: If True (and ``cot_publisher`` is None and the
                scenario's fusion.cot_url is set), the orchestrator
                would lazily build a ``PyTAKCotPublisher``. For v1.0
                in-process orchestrators run with no CoT by default;
                tests pass ``enable_cot=False``. A future ticket may
                lift the lazy build into here.
            enable_dashboard: If True, an in-process
                ``InProcessSubscriber`` is wired to the pub-sub; the
                test harness consumes from its queue. If False, no
                subscriber is registered (the headless mode).
            fix_sink: Optional asyncio queue the orchestrator pushes
                every published ``FixEvent`` onto, after fusion. Tests
                use this to assert fusion fired.
            cot_publisher: Pre-built ``CotPublisher`` instance. When
                supplied, ``enable_cot`` is ignored -- the publisher
                is wired directly into the ``FusionService``.
        """
        if pessimism_factor <= 0.0:
            msg = f"pessimism_factor must be > 0 (got {pessimism_factor})."
            raise ValueError(msg)

        self._scenario = scenario
        self._pessimism_factor = pessimism_factor
        self._enable_cot = enable_cot
        self._enable_dashboard = enable_dashboard
        self._fix_sink = fix_sink
        self._cot_publisher = cot_publisher

        # Built lazily in ``_setup``.
        self._node_contexts: dict[str, _NodeContext] = {}
        self._fusion_service: FusionService | None = None
        self._dashboard_pubsub: DashboardPubSub | None = None
        self._dashboard_queue: asyncio.Queue[BearingReport | FixEvent] | None = None
        self._fix_relay_queue: asyncio.Queue[BearingReport | FixEvent] | None = None
        self._fusion_task: asyncio.Task[None] | None = None
        self._fix_relay_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Bring everything up, walk the beats in order, tear everything down.

        Cleanly cancels tasks on exception. The fusion task is told to
        shutdown rather than cancelled, so the final batch in the inbox
        is processed before exit.
        """
        self._setup()
        assert self._fusion_service is not None  # narrowed for type checkers
        try:
            self._fusion_task = asyncio.create_task(
                self._fusion_service.run(),
                name="replay-fusion-loop",
            )
            self._fix_relay_task = asyncio.create_task(
                self._relay_fixes_to_sink(),
                name="replay-fix-relay",
            )
            await self._walk_beats()
            # Let the last batch drain.
            await asyncio.sleep(max(self._scenario.fusion.batch_window_ms / 1000.0, 0.05))
        finally:
            await self._teardown()

    @property
    def fusion_service(self) -> FusionService | None:
        """Returns the live ``FusionService`` (test introspection)."""
        return self._fusion_service

    @property
    def dashboard_pubsub(self) -> DashboardPubSub | None:
        """Returns the live ``DashboardPubSub`` (test introspection)."""
        return self._dashboard_pubsub

    @property
    def dashboard_queue(self) -> asyncio.Queue[BearingReport | FixEvent] | None:
        """The in-process dashboard queue, populated when ``enable_dashboard`` was True."""
        return self._dashboard_queue

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        """Build per-node contexts + fusion + pubsub. Idempotent within an instance.

        Building the receivers + estimators eagerly here means a
        misconfigured scenario raises *before* ``run`` enters its
        beat loop, which makes failures easier to chase.
        """
        if self._node_contexts:
            return  # already set up

        # 1. Per-node simulator + estimator wiring.
        for node_spec in self._scenario.nodes:
            ctx = self._build_node_context(node_spec)
            self._node_contexts[node_spec.node_id] = ctx

        # 2. Annotate active beats on each context.
        for beat in self._scenario.beats:
            for node_id in beat.active_nodes:
                if node_id in self._node_contexts:
                    self._node_contexts[node_id].active_beats.add(beat.beat_id)

        # 3. Build the fusion service.
        fusion_cfg = self._scenario.fusion.to_fusion_config()
        fuser = StansfieldMLEFuser(fusion_cfg)
        self._dashboard_pubsub = DashboardPubSub()
        self._fusion_service = FusionService(
            config=fusion_cfg,
            fuser=fuser,
            cot_publisher=self._cot_publisher,
            dashboard_pubsub=self._dashboard_pubsub,
        )

        # 4. Optional dashboard in-process subscriber.
        if self._enable_dashboard:
            self._dashboard_queue = asyncio.Queue()
            self._dashboard_pubsub.add_subscriber(
                InProcessSubscriber(self._dashboard_queue),  # type: ignore[arg-type]
            )

        # 5. Dedicated fix-sink relay subscriber.
        # Independent of the dashboard subscriber so headless mode
        # (--headless / enable_dashboard=False) still forwards fixes
        # into fix_sink. Pre-fix, the relay walked the dashboard queue
        # and short-circuited when it was None -- fix_sink stayed empty
        # under --headless. (Council audit 2026-05-18, D2.)
        if self._fix_sink is not None:
            self._fix_relay_queue = asyncio.Queue()
            self._dashboard_pubsub.add_subscriber(
                InProcessSubscriber(self._fix_relay_queue),  # type: ignore[arg-type]
            )

    def _build_node_context(self, spec: NodeReplaySpec) -> _NodeContext:
        """Build the per-node simulator + estimator pipeline."""
        node_cfg = spec.to_node_config(self._scenario.enu_origin)

        # Per-node bearing (geographic, [0, 360)) and range to emitter.
        bearing_deg, range_m = self._geom_for(spec)

        # Simulator scenario for this node. The emitter's azimuth is
        # bearing-from-the-node (degrees true); range is metres; the
        # simulator antenna pattern is a generic ATK-10-like Yagi by
        # default (the simulator's AntennaPattern.atk10() factory).
        emitter = SimEmitterSpec(
            azimuth_deg=bearing_deg,
            range_m=range_m,
            frequency_hz=self._scenario.emitter.center_freq_hz,
            tx_power_db=self._scenario.emitter.emit_power_db_above_noise,
        )

        array_sim: ArraySpec | None = None
        if spec.array is not None:
            array_sim = ArraySpec.ula(
                n_elements=spec.array.n_elements,
                spacing_m=spec.array.element_spacing_m or _DEFAULT_HALF_WAVELENGTH_M_915MHZ,
            )

        # Pessimism is applied as inflation of the simulator noise
        # floor (more negative => quieter; multiply by factor in dB
        # to push the noise *up* toward 0 dBFS).
        base_noise_dbfs = -90.0
        adjusted_noise_dbfs = base_noise_dbfs + 10.0 * math.log10(max(self._pessimism_factor, 1e-6))

        scenario = SimulationScenario(
            emitters=(emitter,),
            antenna=AntennaPattern(hpbw_deg=50.0),
            sample_rate_hz=spec.sdr.sample_rate_hz,
            center_freq_hz=spec.sdr.center_freq_hz,
            noise_floor_dbfs=adjusted_noise_dbfs,
            channel=_reify_channel_model(self._scenario.channel_model),
            array=array_sim,
        )
        receiver = SyntheticReceiver(scenario, seed=hash(spec.node_id) & 0xFFFFFFFF)
        receiver.open()
        receiver.configure(node_cfg)
        if hasattr(receiver, "calibrate"):
            try:
                receiver.calibrate()
            except Exception as exc:
                _LOG.warning(
                    "ReplayOrchestrator: calibrate() failed for %s: %s",
                    spec.node_id,
                    exc,
                )

        # Estimators per capability set.
        l1: L1AmplitudeSweepEstimator | None = None
        l2_music: L2MusicEstimator | None = None
        if Capability.L1_RSSI in spec.capabilities:
            l1 = L1AmplitudeSweepEstimator(
                node_id=spec.node_id,
                node_position=node_cfg.position,
                sweep_step_deg=_L1_SWEEP_STEP_DEG,
                sweep_dwell_samples=_L1_SWEEP_DWELL_SAMPLES,
            )
        if (
            Capability.L2_MUSIC in spec.capabilities
            and spec.array is not None
            and hasattr(receiver, "read_coherent")
        ):
            l2_array = ArrayConfig(
                geometry=ArrayGeometry(spec.array.geometry),
                n_elements=spec.array.n_elements,
                element_spacing_m=spec.array.element_spacing_m,
                element_positions_m=spec.array.element_positions_m,
                calibration_file=spec.array.calibration_file,
            )
            l2_music = L2MusicEstimator(
                node_id=spec.node_id,
                node_position=node_cfg.position,
                receiver=receiver,
                array_config=l2_array,
                operating_frequency_hz=spec.sdr.center_freq_hz,
            )

        return _NodeContext(
            spec=spec,
            node_config=node_cfg,
            receiver=receiver,
            l1=l1,
            l2_music=l2_music,
        )

    def _geom_for(self, spec: NodeReplaySpec) -> tuple[float, float]:
        """Compute (bearing_deg, range_m) from the node to the emitter."""
        node_e, node_n = spec.enu_position_m
        em_e, em_n = self._scenario.emitter.enu_position_m
        de = em_e - node_e
        dn = em_n - node_n
        range_m = math.hypot(de, dn)
        if range_m <= 0.0:
            msg = (
                f"Node {spec.node_id!r} is co-located with the emitter "
                f"(range = {range_m}); cannot compute bearing."
            )
            raise ValueError(msg)
        # atan2(east, north) -> bearing from north, clockwise positive.
        bearing_rad = math.atan2(de, dn)
        bearing_deg = math.degrees(bearing_rad) % 360.0
        return bearing_deg, range_m

    async def _teardown(self) -> None:
        """Cancel everything and close every receiver."""
        if self._fusion_service is not None:
            await self._fusion_service.shutdown()
        for task in (self._fusion_task, self._fix_relay_task):
            if task is not None:
                task.cancel()
        for task in (self._fusion_task, self._fix_relay_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._fusion_task = None
        self._fix_relay_task = None
        for ctx in self._node_contexts.values():
            with contextlib.suppress(Exception):
                ctx.receiver.close()
        self._node_contexts.clear()
        if self._cot_publisher is not None:
            # ``CotPublisher`` Protocol does not mandate ``close``; the
            # concrete ``PyTAKCotPublisher`` has one. Call it when
            # present, never raise on cleanup.
            closer = getattr(self._cot_publisher, "close", None)
            if callable(closer):
                with contextlib.suppress(Exception):
                    closer()

    # ------------------------------------------------------------------
    # Beat walk
    # ------------------------------------------------------------------

    async def _walk_beats(self) -> None:
        """Iterate over the scenario beats in order; drive estimators per beat."""
        if not self._scenario.beats:
            # No beats -- nothing to drive. Useful for the unit test
            # that asserts setup succeeds.
            return
        for beat in self._scenario.beats:
            await self._run_beat(beat)

    async def _run_beat(self, beat: DemoBeatSpec) -> None:
        """Run one beat: emit a sweep from every active node."""
        _LOG.info("ReplayOrchestrator: beat %s (%s)", beat.beat_id, beat.label)
        bearings: list[BearingReport] = []
        for node_id in beat.active_nodes:
            ctx = self._node_contexts.get(node_id)
            if ctx is None:
                continue
            report = self._produce_one_bearing(ctx)
            if report is not None:
                bearings.append(report)

        if self._fusion_service is None or self._dashboard_pubsub is None:
            return

        # Push every bearing into the fusion inbox; the fuse loop
        # consumes them inside ``batch_window_ms``.
        for report in bearings:
            self._fusion_service.push_for_test(report)
            # Also fan out to the dashboard so the live-bearings panel
            # sees the sigma-wedges as they arrive.
            await self._dashboard_pubsub.publish_bearing(report)

        # Let the fuse loop run at least one batch_window_ms cycle.
        await asyncio.sleep(max(self._scenario.fusion.batch_window_ms / 1000.0 * 1.5, 0.1))

    def _produce_one_bearing(self, ctx: _NodeContext) -> BearingReport | None:
        """Drive one bearing out of a node's estimator chain.

        L2 takes priority if both are available (the L2 bearing is the
        tighter sigma; in production both fire and fusion weights
        them by their reported sigma). For v1.0 demo-replay we emit
        one bearing per beat per node -- the simplest honest shape
        that exercises the fusion path.
        """
        if ctx.l2_music is not None:
            return self._estimate_l2(ctx)
        if ctx.l1 is not None:
            return self._estimate_l1(ctx)
        return None

    def _estimate_l1(self, ctx: _NodeContext) -> BearingReport | None:
        """Drive one L1 servo-sweep through the simulator."""
        assert ctx.l1 is not None
        receiver = ctx.receiver
        heading_center = ctx.spec.heading_deg or 0.0
        ctx.l1.begin_sweep(time.time_ns())
        # Sweep ± half_arc with the chosen step.
        n_steps = int(2.0 * _L1_SWEEP_HALF_ARC_DEG / _L1_SWEEP_STEP_DEG)
        # Aim the antenna boresight along the expected emitter
        # direction so the parabola fit lands within the sweep window
        # even with the simulator's idealised pattern. For honesty,
        # the L1 estimator does not see this -- it only sees
        # (heading, RSSI) pairs.
        bearing_deg, _range_m = self._geom_for(ctx.spec)
        center = bearing_deg
        for i in range(n_steps + 1):
            heading = (center - _L1_SWEEP_HALF_ARC_DEG + i * _L1_SWEEP_STEP_DEG) % 360.0
            receiver.set_antenna_heading(heading)
            iq = receiver.read(_L1_SWEEP_DWELL_SAMPLES)
            ctx.l1.observe(heading, iq)
        # Re-aim back to the node's nominal heading for housekeeping.
        receiver.set_antenna_heading(heading_center)
        # The ``samples`` argument to ``estimate`` is ignored by L1.
        report: BearingReport | None = ctx.l1.estimate(
            np.zeros(_L1_SWEEP_DWELL_SAMPLES, dtype=np.complex64)
        )
        return report

    def _estimate_l2(self, ctx: _NodeContext) -> BearingReport | None:
        """Drive one L2 MUSIC estimate through the coherent simulator."""
        assert ctx.l2_music is not None
        receiver = ctx.receiver
        # The coherent receiver's read_coherent returns
        # (n_channels, n_samples); the L2 estimator's
        # ``estimate`` consumes that shape directly. We narrow the
        # base ``SyntheticReceiver`` type with a getattr, because the
        # coherent surface is added by the ``_CoherentSyntheticReceiver``
        # subclass via ``__new__``.
        read_coherent = getattr(receiver, "read_coherent", None)
        if read_coherent is None:
            return None
        coherent = read_coherent(_L2_COHERENT_SAMPLES)
        ctx.l2_music.set_timestamp(time.time_ns())
        report: BearingReport | None = ctx.l2_music.estimate(coherent)
        return report

    # ------------------------------------------------------------------
    # Fix relay
    # ------------------------------------------------------------------

    async def _relay_fixes_to_sink(self) -> None:
        """Forward published FixEvents from the relay queue to ``fix_sink``.

        The relay walks a private subscriber queue registered in ``_setup``
        whenever ``fix_sink`` is supplied. That subscriber is independent
        of the dashboard subscriber, so this relay works under
        ``--headless`` (which suppresses only the *dashboard* subscriber).
        When no sink is configured, this task blocks indefinitely until
        the orchestrator's teardown cancels it.
        """
        sink = self._fix_sink
        queue = self._fix_relay_queue
        if sink is None or queue is None:
            while True:
                await asyncio.sleep(3600.0)

        while True:
            try:
                item = await queue.get()
            except asyncio.CancelledError:
                return
            if isinstance(item, FixEvent):
                await sink.put(item)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Half-wavelength at 915 MHz; used when an L2 scenario omits
# ``element_spacing_m`` (the ULA's typical lambda/2 default).
_DEFAULT_HALF_WAVELENGTH_M_915MHZ: float = 0.164


def _reify_channel_model(spec: ChannelModelSpec) -> ChannelModel:  # noqa: PLR0911
    """Reify a ``ChannelModelSpec`` into a concrete ``ChannelModel``.

    For v1.0 we honour the four atomic kinds and the composite. Extra
    YAML keys are absorbed pragmatically -- the loader is permissive on
    channel parameters because the simulator's ``ChannelModel`` types
    own the strict validation. Unknown atomic kinds fall back to
    ``FreeSpaceChannel`` with a warning rather than crashing the
    orchestrator (the channel model is honesty-overlay, not core
    geometry -- a missing channel realisation is recoverable).
    """
    kind = spec.kind
    extras = spec.model_dump(mode="python", exclude_none=True)
    extras.pop("kind", None)
    extras.pop("layers", None)
    if kind == "free_space":
        return FreeSpaceChannel()
    if kind == "two_ray_ground":
        return TwoRayGroundChannel(
            height_tx_m=float(extras.get("transmitter_height_m", 3.0)),
            height_rx_m=float(extras.get("receiver_height_m", 2.0)),
        )
    if kind == "multipath_fir":
        raw_taps = extras.get("taps", [])
        delays: list[int] = []
        taps_complex: list[complex] = []
        for tap in raw_taps:
            amp = float(tap.get("amplitude", 0.0))
            phase_deg = float(tap.get("phase_deg", 0.0))
            delays.append(int(tap.get("delay_samples", 0)))
            taps_complex.append(
                amp
                * complex(
                    math.cos(math.radians(phase_deg)),
                    math.sin(math.radians(phase_deg)),
                )
            )
        if not taps_complex:
            # MultipathFIRChannel requires >= 1 tap; degenerate
            # specification falls back to free space honestly.
            _LOG.warning("MultipathFIRChannel: empty taps; using FreeSpaceChannel")
            return FreeSpaceChannel()
        return MultipathFIRChannel(
            taps=np.array(taps_complex, dtype=np.complex128),
            delays=np.array(delays, dtype=np.int64),
        )
    if kind == "log_normal_shadowing":
        return LogNormalShadowing(sigma_db=float(extras.get("std_db", 2.0)))
    if kind == "composite" and spec.layers is not None:
        return CompositeChannel(
            channels=tuple(_reify_channel_model(layer) for layer in spec.layers)
        )
    _LOG.warning(
        "ReplayOrchestrator: unknown channel kind %r; using FreeSpaceChannel",
        kind,
    )
    return FreeSpaceChannel()


__all__ = ["ReplayOrchestrator"]
