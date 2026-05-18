"""Tests for the typed payload structs in rfmesh_servo.messages."""

from __future__ import annotations

import pytest
from rfmesh_servo.exceptions import FrameError
from rfmesh_servo.messages import (
    NEVER_MOVED_SENTINEL,
    Ack,
    AxisPosition,
    AxisRequest,
    Calibration,
    ErrorReply,
    MoveRequest,
    Pong,
)

# --------------------------------------------------------------------------
# MoveRequest
# --------------------------------------------------------------------------


def test_move_request_round_trip() -> None:
    req = MoveRequest(axis=0, angle_deg=90.0)
    packed = req.pack()
    assert packed == bytes.fromhex("00 00 84 03")  # 900 deci-deg LE
    assert MoveRequest.unpack(packed) == req


def test_move_request_round_trip_negative() -> None:
    req = MoveRequest(axis=0, angle_deg=-90.0)
    assert MoveRequest.unpack(req.pack()) == req


def test_move_request_quantization() -> None:
    """0.05° rounds to nearest deci-degree (banker's rounding via ``round``)."""
    req = MoveRequest(axis=0, angle_deg=12.34)
    decoded = MoveRequest.unpack(req.pack())
    assert decoded.angle_deg == pytest.approx(12.3, abs=0.05)


def test_move_request_rejects_overflow() -> None:
    with pytest.raises(ValueError):
        MoveRequest(axis=0, angle_deg=10_000.0).pack()


def test_move_request_rejects_short_payload() -> None:
    with pytest.raises(FrameError):
        MoveRequest.unpack(b"\x00\x00\x00")


# --------------------------------------------------------------------------
# AxisRequest
# --------------------------------------------------------------------------


def test_axis_request_round_trip() -> None:
    for axis in (0, 1, 0xFF):
        req = AxisRequest(axis=axis)
        assert AxisRequest.unpack(req.pack()) == req


def test_axis_request_rejects_wrong_length() -> None:
    with pytest.raises(FrameError):
        AxisRequest.unpack(b"")
    with pytest.raises(FrameError):
        AxisRequest.unpack(b"\x00\x00")


# --------------------------------------------------------------------------
# AxisPosition
# --------------------------------------------------------------------------


def test_axis_position_round_trip() -> None:
    pos = AxisPosition(axis=0, commanded_angle_deg=45.0, ms_since_move=1234)
    assert AxisPosition.unpack(pos.pack()) == pos


def test_axis_position_never_moved_sentinel() -> None:
    pos = AxisPosition(axis=0, commanded_angle_deg=0.0, ms_since_move=NEVER_MOVED_SENTINEL)
    assert AxisPosition.unpack(pos.pack()).ms_since_move == NEVER_MOVED_SENTINEL
    assert pos.is_settled_estimate is False


def test_axis_position_settled_threshold() -> None:
    assert AxisPosition(0, 0.0, 249).is_settled_estimate is False
    assert AxisPosition(0, 0.0, 250).is_settled_estimate is True


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def test_calibration_default_round_trip() -> None:
    cal = Calibration.default(axis=0)
    assert cal.is_default
    assert Calibration.unpack(cal.pack()) == cal


def test_calibration_pack_layout() -> None:
    cal = Calibration.default(axis=0)
    packed = cal.pack()
    # axis(1) + reserved(1) + pulse_min(2) + pulse_max(2) + amin(2) + amax(2) + reserved(2)
    assert len(packed) == 12
    # pulse_min = 500 LE = F4 01
    assert packed[2:4] == bytes.fromhex("F4 01")
    # pulse_max = 2500 LE = C4 09
    assert packed[4:6] == bytes.fromhex("C4 09")


def test_calibration_validate_accepts_defaults() -> None:
    Calibration.default(axis=0).validate()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pulse_min_us": 399},
        {"pulse_min_us": 2700},
        {"pulse_max_us": 399},
        {"pulse_max_us": 2700},
        {"pulse_min_us": 2000, "pulse_max_us": 1000},
        {"angle_min_deg": 90.0, "angle_max_deg": -90.0},
    ],
)
def test_calibration_validate_rejects_violations(kwargs: dict[str, float | int]) -> None:
    base: dict[str, float | int] = {
        "axis": 0,
        "pulse_min_us": 500,
        "pulse_max_us": 2500,
        "angle_min_deg": -90.0,
        "angle_max_deg": 90.0,
    }
    base.update(kwargs)
    cal = Calibration(**base)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        cal.validate()


def test_calibration_is_default_false_when_modified() -> None:
    cal = Calibration(
        axis=0,
        pulse_min_us=600,
        pulse_max_us=2400,
        angle_min_deg=-80.0,
        angle_max_deg=80.0,
    )
    assert cal.is_default is False


# --------------------------------------------------------------------------
# Pong
# --------------------------------------------------------------------------


def test_pong_round_trip() -> None:
    pong = Pong(
        proto_version=1,
        fw_major=0,
        fw_minor=2,
        fw_patch=3,
        git_short_sha="deadbeef",
        axis_count=1,
    )
    packed = pong.pack()
    assert len(packed) == 16
    assert Pong.unpack(packed) == pong


def test_pong_rejects_wrong_sha_length() -> None:
    pong = Pong(
        proto_version=1,
        fw_major=0,
        fw_minor=0,
        fw_patch=0,
        git_short_sha="abc",
        axis_count=1,
    )
    with pytest.raises(ValueError):
        pong.pack()


def test_pong_rejects_non_ascii_sha_on_unpack() -> None:
    raw = bytearray(16)
    raw[0] = 1  # proto_version
    raw[4:12] = b"\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8"  # invalid ASCII
    with pytest.raises(FrameError):
        Pong.unpack(bytes(raw))


# --------------------------------------------------------------------------
# Ack / ErrorReply
# --------------------------------------------------------------------------


def test_ack_round_trip() -> None:
    ack = Ack(cmd=0x01)
    assert Ack.unpack(ack.pack()) == ack


def test_error_reply_round_trip_with_message() -> None:
    err = ErrorReply(original_cmd=0x01, error_code=0x02, message="out of range")
    assert ErrorReply.unpack(err.pack()) == err


def test_error_reply_round_trip_empty_message() -> None:
    err = ErrorReply(original_cmd=0x05, error_code=0x03)
    assert ErrorReply.unpack(err.pack()) == err


def test_error_reply_rejects_oversized_message() -> None:
    err = ErrorReply(original_cmd=0x01, error_code=0x02, message="x" * 65)
    with pytest.raises(ValueError):
        err.pack()


def test_error_reply_rejects_short_payload() -> None:
    with pytest.raises(FrameError):
        ErrorReply.unpack(b"\x01")
