"""L1 servo-sweep loop -- the receiver -> servo -> estimator -> bearer wiring.

This is the piece the v1.0 ``Node`` deliberately deferred (see
``node.py`` module docstring, "estimator-loop wiring is a later WS-CD
ticket"). It drives one L1 amplitude-comparison bearing cycle:

  1. ``begin_sweep`` -- stamp the sweep instant.
  2. For each commanded servo angle across the arc:
       - move the servo, let it settle,
       - read exactly ``dwell_samples`` IQ from the receiver,
       - ``observe`` (absolute_heading, iq) into the estimator.
  3. ``estimate`` -- finalise; if a ``BearingReport`` comes back, send it
     over the bearer.

The estimator is owned here (not the one ``Node._build_pipeline`` builds)
so its ``sweep_step_deg`` matches the *actual* servo step -- the
estimator's vertex-in-window guard (``rfmesh_dsp.l1``) rejects a peak
whose parabola vertex lies beyond ``half_window * sweep_step_deg``, so a
mismatched step would silently refuse every real bearing.

Absolute geographic heading fed to ``observe`` is
``(boresight_heading_deg + servo_angle_deg) mod 360`` -- the node's
surveyed antenna boresight (``NodeConfig.heading_deg``, set by
survey-and-align per ARCHITECTURE.md §6) plus the servo's mechanical
offset from boresight (servo angle 0 == boresight).

Hardware quirk respected (INHERITED_CONTEXT.md §3.1.1, servo backlash):
every sweep approaches from the same side -- the loop parks at the low
end of the arc before sweeping upward, so MG996R-clone hysteresis does
not shift the apparent peak between sweeps.

Blocking calls (``servo.move``, ``receiver.read``, ``bearer.send_bearing``)
run via ``asyncio.to_thread`` so the asyncio loop -- and the cooperative
``stopping`` check between angles -- stays responsive.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from rfmesh_contracts import Capability
from rfmesh_dsp.l1 import L1AmplitudeSweepEstimator

if TYPE_CHECKING:
    from rfmesh_contracts import Bearer, GeodeticPosition, Receiver
    from rfmesh_servo.driver import ServoDriver

_LOG = logging.getLogger(__name__)

# The L1 estimator needs >= 7 observations to fit its parabola
# (rfmesh_dsp.l1._PARABOLA_FIT_N_POINTS). We refuse to start a sweep that
# cannot reach that, rather than spin producing only None.
_MIN_OBSERVATIONS: int = 7

# Empty IQ block handed to ``estimate`` -- L1 reads its data from the
# per-heading accumulator and ignores this argument (it only exists to
# satisfy the frozen ``BearingEstimator.estimate`` signature).
_EMPTY_IQ = np.empty(0, dtype=np.complex64)

_DEG_FULL_CIRCLE: float = 360.0


@dataclass(frozen=True)
class L1SweepConfig:
    """Sweep geometry + timing for the L1 loop. All operator-tunable.

    Defaults: a +/-90 deg arc at 2 deg steps == 91 observations. The arc
    must be *wider* than the antenna HPBW (50 deg for the ATK-10 Yagi):
    the L1 estimator's prominence gate measures peak RSSI above the
    median off-axis floor, so the sweep has to include genuine off-beam
    headings or the floor sits inside the main lobe and the gate refuses.
    ``settle_s`` lets an MG996R reach the commanded angle before sampling;
    ``dwell_samples`` is the IQ block per heading.
    """

    axis: int = 0
    min_deg: float = -90.0
    max_deg: float = 90.0
    step_deg: float = 2.0
    settle_s: float = 0.20
    dwell_samples: int = 1024
    inter_sweep_s: float = 1.0
    peak_prominence_db_min: float = 6.0

    def angles(self) -> list[float]:
        """Servo angles across the arc, low -> high (same-side approach)."""
        n = round((self.max_deg - self.min_deg) / self.step_deg) + 1
        return [self.min_deg + i * self.step_deg for i in range(n)]


class L1SweepLoop:
    """Drives repeated L1 bearing sweeps until stopped.

    Owns its servo lifecycle (``connect`` on first run, ``close`` on
    teardown) and its estimator. The receiver and bearer are shared with
    the owning ``Node`` -- the receiver is opened by ``Node.run`` before
    this loop's task starts.
    """

    def __init__(
        self,
        *,
        receiver: Receiver,
        bearer: Bearer | None,
        servo: ServoDriver,
        node_id: str,
        node_position: GeodeticPosition,
        boresight_heading_deg: float,
        config: L1SweepConfig | None = None,
    ) -> None:
        self._receiver = receiver
        self._bearer = bearer
        self._servo = servo
        self._boresight_heading_deg = boresight_heading_deg
        self._cfg = config or L1SweepConfig()

        n_angles = len(self._cfg.angles())
        if n_angles < _MIN_OBSERVATIONS:
            msg = (
                f"L1SweepConfig spans only {n_angles} angles; the L1 estimator "
                f"needs >= {_MIN_OBSERVATIONS}. Widen the arc or shrink step_deg."
            )
            raise ValueError(msg)

        self._estimator = L1AmplitudeSweepEstimator(
            node_id=node_id,
            node_position=node_position,
            sweep_step_deg=self._cfg.step_deg,
            sweep_dwell_samples=self._cfg.dwell_samples,
            peak_prominence_db_min=self._cfg.peak_prominence_db_min,
        )

    async def run(self, stopping: asyncio.Event) -> None:
        """Sweep repeatedly until ``stopping`` is set. Owns servo lifecycle."""
        await asyncio.to_thread(self._servo.connect)
        _LOG.info(
            "L1 sweep: servo connected; arc [%.0f, %.0f] deg step %.1f, boresight %.1f deg",
            self._cfg.min_deg,
            self._cfg.max_deg,
            self._cfg.step_deg,
            self._boresight_heading_deg,
        )
        try:
            while not stopping.is_set():
                await self._one_sweep(stopping)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=self._cfg.inter_sweep_s)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._servo.close)

    async def _one_sweep(self, stopping: asyncio.Event) -> None:
        """Run a single low->high sweep and emit a bearing if one is found."""
        angles = self._cfg.angles()
        # Park at the low end first so every sweep approaches from the same
        # side (servo backlash discipline, INHERITED_CONTEXT.md §3.1.1).
        await asyncio.to_thread(self._servo.move, self._cfg.axis, angles[0])
        await asyncio.sleep(self._cfg.settle_s)

        self._estimator.begin_sweep(time.time_ns())
        for angle in angles:
            if stopping.is_set():
                return
            await asyncio.to_thread(self._servo.move, self._cfg.axis, angle)
            await asyncio.sleep(self._cfg.settle_s)
            iq = await asyncio.to_thread(self._receiver.read, self._cfg.dwell_samples)
            heading = (self._boresight_heading_deg + angle) % _DEG_FULL_CIRCLE
            self._estimator.observe(heading, iq)

        report = self._estimator.estimate(_EMPTY_IQ)
        if report is None:
            _LOG.info("L1 sweep: no bearing (%s)", self._estimator.last_refusal_reason)
            return
        _LOG.info(
            "L1 sweep: bearing %.1f +/- %.1f deg (SNR %.1f dB)",
            report.azimuth_deg,
            report.azimuth_sigma_deg,
            report.snr_db if report.snr_db is not None else float("nan"),
        )
        if self._bearer is not None:
            await asyncio.to_thread(self._bearer.send_bearing, report)

    @property
    def method(self) -> Capability:
        """Capability this loop produces (introspection / tests)."""
        return Capability.L1_RSSI


__all__ = ["L1SweepConfig", "L1SweepLoop"]
