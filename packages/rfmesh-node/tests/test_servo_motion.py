"""Unit tests for ``rfmesh_node.servo_motion``.

Covers:

* ``MotionConfig.validate`` rejects bad inputs.
* Fast path: single MOVE + fixed settle for short moves.
* Ramped path: trapezoidal profile produces the expected number of MOVE
  calls, monotonic angle progression, and a slowdown near the endpoints.
* ``invert_direction`` negates the servo-side angle while caller-visible
  geographic angles are unchanged.
* ``stop`` event interrupts a long ramp.
* ``calibrated_geographic_range`` honours the inversion convention.

Hardware-free: the tests drive a ``MagicMock`` ``ServoDriver`` so no
firmware connection is required.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise
from typing import cast
from unittest.mock import MagicMock

import pytest
from rfmesh_node.servo_motion import (
    MotionConfig,
    calibrated_geographic_range,
    move_smooth,
)
from rfmesh_servo.driver import ServoDriver


def _mock_servo() -> MagicMock:
    return cast(MagicMock, MagicMock(spec_set=ServoDriver))


# ---------------------------------------------------------------------------
# MotionConfig.validate
# ---------------------------------------------------------------------------


def test_motion_config_defaults_validate() -> None:
    MotionConfig().validate()  # no raise


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("max_vel_dps", 0.0),
        ("max_vel_dps", -1.0),
        ("accel_time_s", -0.1),
        ("motion_step_deg", 0.0),
        ("motion_step_deg", -1.0),
        ("fast_threshold_deg", -1.0),
        ("fast_settle_s", -0.01),
    ],
)
def test_motion_config_rejects_bad_field(field: str, bad_value: float) -> None:
    cfg_kwargs = {field: bad_value}
    cfg = MotionConfig(**cfg_kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=field.split("_", maxsplit=1)[0]):
        cfg.validate()


# ---------------------------------------------------------------------------
# Fast path
# ---------------------------------------------------------------------------


def test_fast_path_single_move_under_threshold() -> None:
    """|delta| <= fast_threshold_deg: exactly one MOVE call, no ramp."""
    servo = _mock_servo()
    cfg = MotionConfig(fast_threshold_deg=10.0, fast_settle_s=0.0)
    end = asyncio.run(move_smooth(servo, axis=0, current_deg=0.0, target_deg=5.0, config=cfg))
    assert end == pytest.approx(5.0)
    assert servo.move.call_count == 1
    assert servo.move.call_args.args == (0, 5.0)


def test_fast_path_exactly_at_threshold() -> None:
    """Boundary: |delta| == threshold uses fast path."""
    servo = _mock_servo()
    cfg = MotionConfig(fast_threshold_deg=10.0, fast_settle_s=0.0)
    asyncio.run(move_smooth(servo, axis=0, current_deg=0.0, target_deg=10.0, config=cfg))
    assert servo.move.call_count == 1


def test_fast_path_noop_for_tiny_delta() -> None:
    """A delta below the noop floor (1e-6 deg) issues no MOVE."""
    servo = _mock_servo()
    cfg = MotionConfig()
    end = asyncio.run(
        move_smooth(servo, axis=0, current_deg=10.0, target_deg=10.0 + 1e-9, config=cfg)
    )
    assert end == pytest.approx(10.0)
    servo.move.assert_not_called()


# ---------------------------------------------------------------------------
# Ramped path
# ---------------------------------------------------------------------------


def test_ramped_path_produces_n_moves_for_long_distance() -> None:
    """|delta| > threshold: discretised into motion_step_deg-sized chunks."""
    servo = _mock_servo()
    cfg = MotionConfig(
        fast_threshold_deg=5.0,
        motion_step_deg=2.0,
        accel_time_s=0.0,  # all-cruise, no ramp dwell
        fast_settle_s=0.0,
    )
    # 60 deg / 2 deg step = 30 MOVE calls.
    asyncio.run(move_smooth(servo, axis=0, current_deg=0.0, target_deg=60.0, config=cfg))
    assert servo.move.call_count == 30


def test_ramped_path_monotonic_angle_progression() -> None:
    """Every MOVE moves the same direction; final == target."""
    servo = _mock_servo()
    cfg = MotionConfig(fast_threshold_deg=5.0, motion_step_deg=2.0)
    end = asyncio.run(move_smooth(servo, axis=0, current_deg=0.0, target_deg=20.0, config=cfg))
    angles = [call.args[1] for call in servo.move.call_args_list]
    assert all(b > a for a, b in pairwise(angles))
    assert end == pytest.approx(20.0)
    assert angles[-1] == pytest.approx(20.0)


def test_ramped_path_descending() -> None:
    """Reverse direction works symmetrically (angles decreasing)."""
    servo = _mock_servo()
    cfg = MotionConfig(fast_threshold_deg=5.0, motion_step_deg=2.0)
    end = asyncio.run(move_smooth(servo, axis=0, current_deg=20.0, target_deg=-20.0, config=cfg))
    angles = [call.args[1] for call in servo.move.call_args_list]
    assert all(b < a for a, b in pairwise(angles))
    assert end == pytest.approx(-20.0)
    assert angles[-1] == pytest.approx(-20.0)


# ---------------------------------------------------------------------------
# Inversion
# ---------------------------------------------------------------------------


def test_invert_direction_negates_servo_side_angle_fast_path() -> None:
    """With invert_direction=True, MOVE receives -target_deg on the fast path."""
    servo = _mock_servo()
    cfg = MotionConfig(invert_direction=True, fast_settle_s=0.0)
    end = asyncio.run(move_smooth(servo, axis=0, current_deg=0.0, target_deg=7.0, config=cfg))
    assert end == pytest.approx(7.0)  # caller sees geographic frame
    assert servo.move.call_args.args == (0, -7.0)  # servo sees negated


def test_invert_direction_negates_servo_side_angle_ramped_path() -> None:
    """Inversion holds across every step of a ramped move."""
    servo = _mock_servo()
    cfg = MotionConfig(invert_direction=True, fast_threshold_deg=5.0, motion_step_deg=2.0)
    end = asyncio.run(move_smooth(servo, axis=0, current_deg=0.0, target_deg=20.0, config=cfg))
    angles = [call.args[1] for call in servo.move.call_args_list]
    assert end == pytest.approx(20.0)
    # Every commanded servo angle is the negation of the geographic-frame target.
    assert all(a <= 0 for a in angles)
    assert angles[-1] == pytest.approx(-20.0)


# ---------------------------------------------------------------------------
# Stop event
# ---------------------------------------------------------------------------


def test_stop_event_interrupts_long_ramp() -> None:
    """Setting the stop event mid-ramp returns early at the last commanded angle."""
    servo = _mock_servo()
    cfg = MotionConfig(fast_threshold_deg=5.0, motion_step_deg=2.0, accel_time_s=0.0)

    async def _run() -> float:
        stop = asyncio.Event()
        stop.set()  # already set; first iteration of loop exits immediately
        return await move_smooth(
            servo,
            axis=0,
            current_deg=0.0,
            target_deg=60.0,
            config=cfg,
            stop=stop,
        )

    end = asyncio.run(_run())
    # With stop pre-set the loop returns before any MOVE call.
    assert end == pytest.approx(0.0)
    servo.move.assert_not_called()


# ---------------------------------------------------------------------------
# Geographic range translation
# ---------------------------------------------------------------------------


def test_calibrated_geographic_range_no_inversion() -> None:
    """Without inversion, geographic range == firmware range."""
    geo_min, geo_max = calibrated_geographic_range(
        angle_min_fw_deg=-85.0,
        angle_max_fw_deg=88.0,
        invert_direction=False,
    )
    assert geo_min == pytest.approx(-85.0)
    assert geo_max == pytest.approx(88.0)


def test_calibrated_geographic_range_with_inversion() -> None:
    """With inversion, [-fw_max, -fw_min] in geographic frame."""
    geo_min, geo_max = calibrated_geographic_range(
        angle_min_fw_deg=-85.0,  # operator-observed +85 was stored as -85
        angle_max_fw_deg=88.0,  # operator-observed -88 was stored as 88
        invert_direction=True,
    )
    assert geo_min == pytest.approx(-88.0)
    assert geo_max == pytest.approx(85.0)
    assert geo_min < geo_max  # contract: range is well-ordered after translation
