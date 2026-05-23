"""Servo motion helpers — trapezoidal ramp + fast path + inversion.

A small library of pure functions that compose servo MOVE commands into
operator-friendly motion profiles. Used by:

* ``L1SweepLoop`` (when motion_profile == "trapezoidal", opt-in) — to
  protect gears and mast from snap motion across the wraparound at sweep
  end.
* ``NodeController`` (after ADR-018) — to drive manual-steer commands
  with smooth start / stop ramps.
* ``scripts/bench_single_node.py`` (legacy) — direct caller until
  consolidation into ``run_node`` lands.

Why this module lives in ``rfmesh-node`` (not ``rfmesh-servo``): the
trapezoidal profile generates a *sequence* of small MOVE calls and a
sleep between each, so an asyncio cancellation can preempt the move
mid-flight (manual-steer override interrupting a long ramp). The
``rfmesh-servo`` package is deliberately synchronous and protocol-level;
adding asyncio cancellation semantics there would muddy its scope.
Per council code-reviewer (2026-05-23), the helper stays node-side.

Geometric conventions:

* All angles in **degrees**, in the **geographic frame** (CW positive
  per ``INTERFACES.md`` §0). When ``invert_direction=True`` the helper
  negates the angle just before issuing ``servo.move()``, so callers
  see geographic-frame angles end-to-end.
* All velocities in **degrees per second**, positive scalar.
* All times in **seconds**, positive.

Per Invariant B3 (no silent fallbacks): invalid parameters raise
``ValueError`` loudly; out-of-arc targets are NOT clamped here (callers
must validate against ``Calibration`` first — the helper is angle-
agnostic).
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rfmesh_servo.driver import ServoDriver

_LOG = logging.getLogger(__name__)

# Threshold below which a smooth-move call is treated as a no-op. Smaller
# than the firmware's calibration resolution (0.1° per ``cal_types.h``),
# so a sub-noop move can never produce a meaningful PWM change.
_MOTION_NOOP_DEG: float = 1e-6

# Velocity floor used by the trapezoidal profile to prevent division by
# zero at the very start / end of the ramp where the closed-form velocity
# expression would give 0. 1 dps means the slowest commanded step takes
# at most 1 second of dwell; well above what a real servo settles into.
_MIN_VEL_FLOOR_DPS: float = 1.0


@dataclass(frozen=True)
class MotionConfig:
    """Tunables for the trapezoidal motion profile.

    Defaults are conservative for an MG996R clone driving an ATK-10 Yagi
    on a tripod mast. Operators with sturdier mast or larger antenna
    should slow them further (``max_vel_dps`` down, ``accel_time_s`` up).

    Attributes:
        max_vel_dps: Cruise angular velocity in degrees / second.
            Lower = gentler on gears and mast. Default 25 dps.
        accel_time_s: Ramp-up and ramp-down duration each side of cruise,
            seconds. Longer = smoother start / stop, more momentum
            control. Default 0.40 s.
        motion_step_deg: Discretisation step of the trapezoidal profile,
            degrees. Smaller = smoother motion, more USB-CDC traffic.
            Default 1.0°.
        fast_threshold_deg: Moves with |delta| <= this distance use the
            fast path (single MOVE + fixed settle) and bypass the
            trapezoidal ramp. Small moves carry little kinetic energy
            and don't need protection. Default 10.0°.
        fast_settle_s: Dwell after a fast-path MOVE before returning,
            seconds. Default 0.10 s — just enough for an MG996R to
            physically reach the new angle.
        invert_direction: When True, the angle sent to ``servo.move()``
            is negated. For a servo mounted such that increasing pulse
            rotates the mast counter-clockwise (opposite the
            INTERFACES.md §0 CW-positive convention). Caller-visible
            angles stay in the geographic frame.
    """

    max_vel_dps: float = 25.0
    accel_time_s: float = 0.40
    motion_step_deg: float = 1.0
    fast_threshold_deg: float = 10.0
    fast_settle_s: float = 0.10
    invert_direction: bool = False

    def validate(self) -> None:
        """Raise ValueError if any field is out of range.

        Called once at MotionConfig construction time (frozen dataclass
        has no automatic validators); helpers below also call validate
        defensively so a hand-built config can't silently misbehave.
        """
        if self.max_vel_dps <= 0.0:
            msg = f"max_vel_dps must be > 0 (got {self.max_vel_dps})."
            raise ValueError(msg)
        if self.accel_time_s < 0.0:
            msg = f"accel_time_s must be >= 0 (got {self.accel_time_s})."
            raise ValueError(msg)
        if self.motion_step_deg <= 0.0:
            msg = f"motion_step_deg must be > 0 (got {self.motion_step_deg})."
            raise ValueError(msg)
        if self.fast_threshold_deg < 0.0:
            msg = f"fast_threshold_deg must be >= 0 (got {self.fast_threshold_deg})."
            raise ValueError(msg)
        if self.fast_settle_s < 0.0:
            msg = f"fast_settle_s must be >= 0 (got {self.fast_settle_s})."
            raise ValueError(msg)


async def move_smooth(
    servo: ServoDriver,
    axis: int,
    current_deg: float,
    target_deg: float,
    config: MotionConfig,
    *,
    stop: asyncio.Event | None = None,
) -> float:
    """Drive ``servo`` from current_deg to target_deg using a two-tier policy.

    * **Fast path** (|delta| <= ``config.fast_threshold_deg``): one MOVE
      + ``config.fast_settle_s`` dwell. No ramp.
    * **Ramped path** (|delta| > threshold): trapezoidal motion profile,
      discretised into ``config.motion_step_deg``-sized angle steps;
      each step's delay derives from the instantaneous velocity at that
      point in the accel / cruise / decel curve:

          v(s) = sqrt(2 * a * s)            in the accel region
          v(s) = v_max                      in the cruise region
          v(s) = sqrt(2 * a * (D - s))      in the decel region

      where ``a = v_max / accel_time``, ``D`` is total angular distance,
      and ``s`` is distance travelled so far. Short moves (D <
      2 * ramp_distance) fold into a triangular profile with reduced
      peak velocity.

    Args:
        servo: Connected ``ServoDriver``. The caller owns its lifecycle.
        axis: Axis ID (0 in single-axis setups).
        current_deg: Geographic-frame angle the servo is currently at.
            Must be tracked by the caller (the firmware does not report
            this on every step).
        target_deg: Geographic-frame angle to move to.
        config: ``MotionConfig`` with tunables + ``invert_direction``.
        stop: Optional event; when set mid-ramp the function returns
            early at the last commanded angle. ``None`` disables
            mid-move cancellation (used for park-on-shutdown where the
            move must complete).

    Returns:
        The final geographic-frame angle reached. Equals ``target_deg``
        on normal completion. Equals the last commanded angle if
        ``stop`` triggered mid-ramp.

    Raises:
        ValueError: ``config`` fails validation.
    """
    config.validate()

    delta = target_deg - current_deg
    distance = abs(delta)
    if distance < _MOTION_NOOP_DEG:
        return current_deg

    # Fast path: small moves get a single MOVE + fixed settle.
    if distance <= config.fast_threshold_deg:
        servo_angle = -target_deg if config.invert_direction else target_deg
        servo.move(axis, servo_angle)
        await asyncio.sleep(config.fast_settle_s)
        return target_deg

    # Ramped path: discretise + sleep-by-velocity.
    direction = 1.0 if delta > 0.0 else -1.0
    n_steps = max(1, math.ceil(distance / config.motion_step_deg))
    step = distance / n_steps

    accel_dps2 = config.max_vel_dps / config.accel_time_s if config.accel_time_s > 0.0 else math.inf
    ramp_dist_full = 0.5 * config.max_vel_dps * config.accel_time_s
    ramp_dist = min(ramp_dist_full, distance / 2.0)
    peak_vel = (
        config.max_vel_dps
        if ramp_dist >= ramp_dist_full
        else math.sqrt(2.0 * accel_dps2 * ramp_dist)
    )

    new_angle = current_deg
    for k in range(1, n_steps + 1):
        if stop is not None and stop.is_set():
            return new_angle
        s_mid = (k - 0.5) * step
        if s_mid < ramp_dist:
            v = math.sqrt(2.0 * accel_dps2 * s_mid)
        elif s_mid > distance - ramp_dist:
            v = math.sqrt(2.0 * accel_dps2 * (distance - s_mid))
        else:
            v = peak_vel
        v = max(v, _MIN_VEL_FLOOR_DPS)
        delay = step / v
        new_angle = current_deg + direction * k * step
        servo_angle = -new_angle if config.invert_direction else new_angle
        servo.move(axis, servo_angle)
        await asyncio.sleep(delay)
    return new_angle


def calibrated_geographic_range(
    angle_min_fw_deg: float,
    angle_max_fw_deg: float,
    *,
    invert_direction: bool,
) -> tuple[float, float]:
    """Translate firmware-frame calibration limits into geographic-frame bounds.

    The firmware-side validator requires ``angle_min_deg < angle_max_deg``
    (positive pulse-to-angle slope). When the servo is mounted such that
    increasing pulse rotates the mast counter-clockwise, the host-side
    ``invert_direction`` flag negates the angle on every MOVE; the
    geographic-frame angle the operator commands is therefore
    ``-firmware_angle``. This helper translates the calibration
    table's two endpoints accordingly.

    Args:
        angle_min_fw_deg: ``Calibration.angle_min_deg`` from firmware.
        angle_max_fw_deg: ``Calibration.angle_max_deg`` from firmware.
        invert_direction: Whether the host-side inversion is active.

    Returns:
        ``(geo_min, geo_max)`` such that ``geo_min < geo_max``; the
        operator's sweep/manual-steer commands must lie in this range.
    """
    if invert_direction:
        return (-angle_max_fw_deg, -angle_min_fw_deg)
    return (angle_min_fw_deg, angle_max_fw_deg)


__all__ = ["MotionConfig", "calibrated_geographic_range", "move_smooth"]
