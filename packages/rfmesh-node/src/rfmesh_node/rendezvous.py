"""Antenna rendezvous -- directional mutual-pointing for node-to-node links.

Two L1 nodes each carry a directional Yagi on a 1-axis ±90° servo. To bring up
a directional inter-node link both antennas must point at each other. Because
both ends are directional, two free-running sweeps almost never coincide (the
deafness problem). ADR-019's mechanism:

* **GPS-prior pointing.** Both nodes know both surveyed positions (the peer's
  comes from ``RendezvousConfig``), so each computes the great-circle bearing
  to the other and points there directly -- mutual main-lobe overlap by
  construction, no blind search.
* **Scan-and-stare.** Role is derived from the stable ``node_id`` with no
  negotiation: lower id STAREs (holds), higher id SCANs (a small ±N° refine
  mini-sweep across the stationary peer). Equal ids is a loud failure.
* **Bidirectional connection-mode motion.** Unlike the jammer-DF
  ``L1SweepLoop`` (which keeps the same-side-approach discipline for bearing
  *accuracy*), connection mode only needs the beam inside the peer's lobe, so
  it moves directly in either direction -- limited only by the ±90° arc. Any
  residual backlash is absorbed by the honest ``azimuth_sigma_deg`` (B2).
* **Reuse of the L1 estimator.** The refine peak-fit + honest sigma + loud
  refusal reason come from ``L1AmplitudeSweepEstimator`` -- no new DSP.

This module is node-layer only (no contract change, B1; no ``rfmesh-fusion``
sibling import, WD-1 -- the geodesic bearing is computed here).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
from rfmesh_dsp.l1 import L1AmplitudeSweepEstimator

if TYPE_CHECKING:
    from rfmesh_contracts import BearingReport, GeodeticPosition, Receiver
    from rfmesh_servo.driver import ServoDriver

_LOG = logging.getLogger(__name__)

Role = Literal["STARER", "SCANNER"]

# The reused L1 estimator needs >= 7 observations for its parabola fit
# (rfmesh_dsp.l1._PARABOLA_FIT_N_POINTS). A refine arc narrower than this is a
# loud config error rather than a loop that only ever refuses.
_MIN_OBSERVATIONS: int = 7
# Empty IQ handed to estimate(); L1 reads from its per-heading accumulator and
# ignores this (it exists only to satisfy the BearingEstimator signature).
_EMPTY_IQ = np.empty(0, dtype=np.complex64)
_DEG_FULL_CIRCLE: float = 360.0
_DEG_HALF_CIRCLE: float = 180.0


class PeerOutOfArcError(ValueError):
    """The peer's bearing maps outside the servo's ±90° arc (peer behind boresight).

    Unrecoverable by sweeping -- the antenna physically cannot point there. A
    mount/survey problem (B6); we refuse loudly (B3) rather than clamp.
    """


class DuplicateNodeIdError(ValueError):
    """Self and peer share a ``node_id`` -- both would elect SCANNER (deafness)."""


def _wrap180(angle_deg: float) -> float:
    """Wrap to (-180, 180]."""
    return (angle_deg + _DEG_HALF_CIRCLE) % _DEG_FULL_CIRCLE - _DEG_HALF_CIRCLE


def _mode_cancelled(cancel: asyncio.Event | None) -> bool:
    """ADR-024 cancel-event helper -- ``cancel`` may be ``None`` for callers
    that have not opted into mode-handoff semantics (legacy/test path)."""
    return cancel is not None and cancel.is_set()


def geodesic_initial_bearing_deg(origin: GeodeticPosition, target: GeodeticPosition) -> float:
    """Great-circle initial bearing origin->target, degrees true, [0, 360).

    Standard forward-azimuth on a sphere; at the few-km inter-node ranges in
    scope the spherical/ellipsoidal difference is far below the antenna HPBW,
    so a sphere is honest here.
    """
    lat1 = math.radians(origin.lat_deg)
    lat2 = math.radians(target.lat_deg)
    dlon = math.radians(target.lon_deg - origin.lon_deg)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % _DEG_FULL_CIRCLE


def role(self_id: str, peer_id: str) -> Role:
    """Derive the rendezvous role from the two stable node_ids (no negotiation).

    Lower id STAREs (holds), higher id SCANs. Both nodes compute the same split
    from the same two strings, so simultaneous registration cannot race. Equal
    ids is a loud failure (both would SCAN -> deafness).
    """
    if self_id == peer_id:
        msg = f"rendezvous: self and peer share node_id {self_id!r}; ids must be unique"
        raise DuplicateNodeIdError(msg)
    return "STARER" if self_id < peer_id else "SCANNER"


def expected_servo_angle(
    self_pos: GeodeticPosition,
    peer_pos: GeodeticPosition,
    boresight_heading_deg: float,
    *,
    min_servo_deg: float,
    max_servo_deg: float,
) -> float:
    """Servo angle (relative to boresight) that points the antenna at the peer.

    Inverse of the forward map ``heading = (boresight + servo_angle) % 360`` the
    L1 sweep uses. Raises :class:`PeerOutOfArcError` if the peer is outside the
    reachable arc (B3 -- refuse, do not clamp).
    """
    bearing = geodesic_initial_bearing_deg(self_pos, peer_pos)
    angle = _wrap180(bearing - boresight_heading_deg)
    if not (min_servo_deg <= angle <= max_servo_deg):
        msg = (
            f"rendezvous: peer at servo {angle:.1f} deg is outside the "
            f"[{min_servo_deg:.0f}, {max_servo_deg:.0f}] arc -- re-survey mount heading"
        )
        raise PeerOutOfArcError(msg)
    return angle


def _arc_angles(lo_deg: float, hi_deg: float, step_deg: float) -> list[float]:
    """Servo angles across ``[lo, hi]`` inclusive at ``step`` (low -> high)."""
    n = round((hi_deg - lo_deg) / step_deg) + 1
    return [lo_deg + i * step_deg for i in range(n)]


@dataclass(frozen=True)
class RendezvousConfig:
    """Peer roster + connection-mode timing. Node-layer (no contract change).

    ``peer_node_id`` + ``peer_position`` are the operator/config trigger
    (ADR-019): there is no node-to-node discovery channel. Defaults mirror
    ``L1SweepConfig`` where they overlap.
    """

    peer_node_id: str
    peer_position: GeodeticPosition
    axis: int = 0
    refine_half_arc_deg: float = 20.0
    refine_step_deg: float = 2.0
    settle_s: float = 0.20
    dwell_samples: int = 1024
    peak_prominence_db_min: float = 6.0
    min_servo_deg: float = -90.0
    max_servo_deg: float = 90.0
    link_hold_s: float = 30.0
    retry_pause_s: float = 2.0
    # Widening refine arcs tried in order until a peak is found (degrees, half-arc).
    escalation_half_arcs: tuple[float, ...] = (20.0, 45.0, 90.0)


class RendezvousLoop:
    """Connection-mode controller: point at the peer, refine, hold.

    Drives the servo + receiver shared with the owning ``Node`` (which owns the
    servo connect/close lifecycle, ADR-019). One instance per peer. The node
    supervisor calls :meth:`acquire` then :meth:`hold`, time-sharing the servo
    with the jammer ``L1SweepLoop`` between passes.
    """

    def __init__(
        self,
        *,
        receiver: Receiver,
        servo: ServoDriver,
        node_id: str,
        node_position: GeodeticPosition,
        boresight_heading_deg: float,
        config: RendezvousConfig,
    ) -> None:
        self._receiver = receiver
        self._servo = servo
        self._node_id = node_id
        self._node_position = node_position
        self._boresight = boresight_heading_deg
        self._cfg = config
        # Role is derived now so a duplicate node_id fails loudly at construction.
        self._role: Role = role(node_id, config.peer_node_id)

        widest = max(config.refine_half_arc_deg, *config.escalation_half_arcs)
        n_widest = len(_arc_angles(-widest, widest, config.refine_step_deg))
        n_default = len(
            _arc_angles(
                -config.refine_half_arc_deg, config.refine_half_arc_deg, config.refine_step_deg
            )
        )
        if n_default < _MIN_OBSERVATIONS:
            msg = (
                f"RendezvousConfig refine arc spans only {n_default} angles; the L1 "
                f"estimator needs >= {_MIN_OBSERVATIONS}. Widen refine_half_arc_deg "
                f"or shrink refine_step_deg."
            )
            raise ValueError(msg)
        # One estimator, reused across refine passes. step must match the refine
        # step or the estimator's vertex-in-window guard silently refuses.
        self._estimator = L1AmplitudeSweepEstimator(
            node_id=node_id,
            node_position=node_position,
            sweep_step_deg=config.refine_step_deg,
            sweep_dwell_samples=config.dwell_samples,
            peak_prominence_db_min=config.peak_prominence_db_min,
        )
        self._last_status = "rendezvous: idle"
        self._last_report: BearingReport | None = None
        _LOG.info(
            "rendezvous: role=%s peer=%s widest refine arc spans %d angles",
            self._role,
            config.peer_node_id,
            n_widest,
        )

    @property
    def role(self) -> Role:
        """STARER (holds) or SCANNER (refines), derived from node_id ordering."""
        return self._role

    @property
    def config(self) -> RendezvousConfig:
        """The frozen rendezvous config (timing the supervisor reads)."""
        return self._cfg

    @property
    def last_status(self) -> str:
        """Operator-facing status of the most recent acquire/hold (-> status_detail)."""
        return self._last_status

    @property
    def last_report(self) -> BearingReport | None:
        """The most recent refined peer-bearing report (SCANNER only), or None."""
        return self._last_report

    def target_servo_angle(self) -> float:
        """Servo angle pointing at the peer; raises :class:`PeerOutOfArcError`."""
        return expected_servo_angle(
            self._node_position,
            self._cfg.peer_position,
            self._boresight,
            min_servo_deg=self._cfg.min_servo_deg,
            max_servo_deg=self._cfg.max_servo_deg,
        )

    async def acquire(  # noqa: PLR0911
        self, stopping: asyncio.Event, cancel: asyncio.Event | None = None
    ) -> bool:
        """Point at the peer and (if SCANNER) refine to a lock.

        Returns True on lock, False on out-of-arc / refine-ladder exhaustion /
        stop / cancel. On failure, :attr:`last_status` carries the loud reason.
        Motion is bidirectional (connection mode): point directly, no same-side
        approach.

        ``cancel`` (ADR-024) is the NodeController mode-handoff signal. Checked
        at the top of each escalation iteration and inside ``_refine_once``;
        when set, returns False after the next safe checkpoint between two
        ``servo.move`` calls (the in-flight ``to_thread`` always completes
        first; see ADR-024 §2 binding clause).
        """
        try:
            target = self.target_servo_angle()
        except PeerOutOfArcError as exc:
            self._last_status = f"rendezvous failed: {exc}"
            _LOG.warning("%s", self._last_status)
            return False

        if _mode_cancelled(cancel):
            return False
        await self._point(target)

        if self._role == "STARER":
            # Hold steady so the SCANNER's sweep crosses a stationary target;
            # mutual lock is confirmed by the link itself once both hold.
            self._last_status = f"rendezvous: STARER holding at servo {target:.1f} deg"
            _LOG.info("%s", self._last_status)
            return True

        # SCANNER: refine across a widening ladder until a peak clears the gate.
        for half_arc in self._cfg.escalation_half_arcs:
            if stopping.is_set() or _mode_cancelled(cancel):
                return False
            report = await self._refine_once(stopping, half_arc, cancel)
            if report is not None:
                self._last_report = report
                # Re-point onto the refined peak (bidirectional, within arc).
                if _mode_cancelled(cancel):
                    return False
                await self._point(_wrap180(report.azimuth_deg - self._boresight))
                self._last_status = (
                    f"rendezvous: SCANNER locked, peer bearing {report.azimuth_deg:.1f} "
                    f"+/- {report.azimuth_sigma_deg:.1f} deg"
                )
                _LOG.info("%s", self._last_status)
                return True
            self._last_status = (
                f"rendezvous: no peak at +/-{half_arc:.0f} deg "
                f"({self._estimator.last_refusal_reason})"
            )
            _LOG.info("%s", self._last_status)
        self._last_status = "rendezvous failed: no peer peak across escalation ladder"
        _LOG.warning("%s", self._last_status)
        return False

    async def hold(
        self,
        stopping: asyncio.Event,
        duration_s: float,
        cancel: asyncio.Event | None = None,
    ) -> None:
        """Keep the antenna on the peer for ``duration_s`` (servo already pointed).

        ``cancel`` lets ``NodeController`` preempt the hold immediately; a
        manual_steer arriving during a 30s link-hold should not wait for the
        hold to expire.
        """
        wait_targets = [stopping.wait()]
        if cancel is not None:
            wait_targets.append(cancel.wait())
        with contextlib.suppress(TimeoutError):
            _done, pending = await asyncio.wait(
                [asyncio.create_task(t) for t in wait_targets],
                timeout=duration_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    def reset(self) -> None:
        """Drop the estimator's accumulator (ADR-024 mode-exit reset)."""
        self._estimator.begin_sweep(time.time_ns())

    async def _point(self, angle_deg: float) -> None:
        """Move directly to ``angle_deg`` and settle (no same-side approach)."""
        await asyncio.to_thread(self._servo.move, self._cfg.axis, angle_deg)
        await asyncio.sleep(self._cfg.settle_s)

    async def _refine_once(
        self,
        stopping: asyncio.Event,
        half_arc_deg: float,
        cancel: asyncio.Event | None = None,
    ) -> BearingReport | None:
        """One refine mini-sweep across ±``half_arc_deg`` around the peer target.

        Bidirectional motion, clamped to the servo arc. Reuses the L1 estimator
        for the peak fit; returns its ``BearingReport`` or ``None``.

        Safe checkpoint (ADR-024 §2 binding): BETWEEN two ``servo.move`` calls.
        """
        target = self.target_servo_angle()
        lo = max(target - half_arc_deg, self._cfg.min_servo_deg)
        hi = min(target + half_arc_deg, self._cfg.max_servo_deg)
        angles = _arc_angles(lo, hi, self._cfg.refine_step_deg)
        if len(angles) < _MIN_OBSERVATIONS:
            self._last_status = (
                f"rendezvous: refine arc at edge spans only {len(angles)} angles "
                f"(< {_MIN_OBSERVATIONS}); peer too close to ±90° limit"
            )
            return None

        self._estimator.begin_sweep(time.time_ns())
        for angle in angles:
            if stopping.is_set() or _mode_cancelled(cancel):
                return None
            await self._point(angle)
            iq = await asyncio.to_thread(self._receiver.read, self._cfg.dwell_samples)
            heading = (self._boresight + angle) % _DEG_FULL_CIRCLE
            self._estimator.observe(heading, iq)
        return self._estimator.estimate(_EMPTY_IQ)


__all__ = [
    "DuplicateNodeIdError",
    "PeerOutOfArcError",
    "RendezvousConfig",
    "RendezvousLoop",
    "Role",
    "expected_servo_angle",
    "geodesic_initial_bearing_deg",
    "role",
]
