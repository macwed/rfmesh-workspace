"""Behavioural tests for ServoDriver against a fake Transport.

Covers the §4 sequencing rules, §6 linenoise detection, and the per-command
request/reply contracts from §3 of docs/wire-protocols/servo_uart_v1.md.
"""

from __future__ import annotations

import pytest
from conftest import FakeTransport, make_pong
from rfmesh_servo.driver import ServoDriver
from rfmesh_servo.exceptions import (
    IncompatibleProtocolError,
    LinenoiseDetectedError,
    ServoCommandError,
    ServoProtocolError,
    ServoTimeoutError,
)
from rfmesh_servo.messages import (
    Ack,
    AxisPosition,
    Calibration,
    ErrorReply,
)
from rfmesh_servo.protocol import Cmd, encode_frame

FAST_REPLY_S = 0.05
FAST_GRACE_S = 0.05


def _driver(transport: FakeTransport, **overrides: object) -> ServoDriver:
    kwargs: dict[str, object] = {
        "reply_timeout_s": FAST_REPLY_S,
        "connect_grace_s": FAST_GRACE_S,
    }
    kwargs.update(overrides)
    return ServoDriver(transport, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# connect()
# --------------------------------------------------------------------------


def test_connect_consumes_unsolicited_pong(fake_transport: FakeTransport) -> None:
    fake_transport.queue_unsolicited_frame(Cmd.PONG, make_pong().pack())
    driver = _driver(fake_transport)
    pong = driver.connect()
    assert pong.proto_version == 1
    assert pong.git_short_sha == "deadbeef"
    # No PING was needed.
    assert len(fake_transport.tx_log) == 0
    assert fake_transport.input_buffer_resets == 1


def test_connect_falls_back_to_ping(auto_pong_transport: FakeTransport) -> None:
    driver = _driver(auto_pong_transport)
    pong = driver.connect()
    assert pong.proto_version == 1
    cmd, payload = auto_pong_transport.last_sent_frame()
    assert cmd == Cmd.PING
    assert payload == b""


def test_connect_rejects_protocol_version_mismatch(fake_transport: FakeTransport) -> None:
    fake_transport.queue_unsolicited_frame(Cmd.PONG, make_pong(proto_version=2).pack())
    driver = _driver(fake_transport)
    with pytest.raises(IncompatibleProtocolError):
        driver.connect()


def test_connect_raises_linenoise_on_garbage(fake_transport: FakeTransport) -> None:
    fake_transport.queue_unsolicited_raw(b"unknown command: ping\r\n")
    driver = _driver(fake_transport)
    with pytest.raises(LinenoiseDetectedError):
        driver.connect()


def test_connect_times_out_when_silent(fake_transport: FakeTransport) -> None:
    """Empty rx queue and no auto-responder → grace expires, PING expires too."""
    driver = _driver(fake_transport)
    with pytest.raises(ServoTimeoutError):
        driver.connect()


# --------------------------------------------------------------------------
# Move / Stop / Position
# --------------------------------------------------------------------------


def test_move_emits_correct_frame_and_acks(auto_pong_transport: FakeTransport) -> None:
    transport = auto_pong_transport

    def responder(self_: FakeTransport, frame_bytes: bytes) -> None:
        from rfmesh_servo.protocol import decode_frame

        cmd, _payload = decode_frame(frame_bytes)
        if cmd == Cmd.PING:
            self_.queue_frame(Cmd.PONG, make_pong().pack())
        elif cmd == Cmd.MOVE:
            self_.queue_frame(Cmd.ACK, Ack(cmd=Cmd.MOVE).pack())

    transport.on_write = responder
    driver = _driver(transport)
    driver.connect()
    driver.move(axis=0, angle_deg=90.0)
    cmd, payload = transport.last_sent_frame()
    assert cmd == Cmd.MOVE
    assert payload == bytes.fromhex("00 00 84 03")  # axis=0, reserved=0, 900 deci-deg LE


def test_move_propagates_error_reply(auto_pong_transport: FakeTransport) -> None:
    from rfmesh_servo.protocol import decode_frame

    transport = auto_pong_transport

    def responder(self_: FakeTransport, frame_bytes: bytes) -> None:
        cmd, _payload = decode_frame(frame_bytes)
        if cmd == Cmd.PING:
            self_.queue_frame(Cmd.PONG, make_pong().pack())
        elif cmd == Cmd.MOVE:
            err = ErrorReply(original_cmd=Cmd.MOVE, error_code=0x02, message="too far")
            self_.queue_frame(Cmd.ERROR, err.pack())

    transport.on_write = responder
    driver = _driver(transport)
    driver.connect()
    with pytest.raises(ServoCommandError) as excinfo:
        driver.move(axis=0, angle_deg=999.0)
    assert excinfo.value.error_code == 0x02
    assert excinfo.value.original_cmd == Cmd.MOVE
    assert "too far" in str(excinfo.value)


def test_move_raises_when_ack_echoes_wrong_cmd(auto_pong_transport: FakeTransport) -> None:
    from rfmesh_servo.protocol import decode_frame

    transport = auto_pong_transport

    def responder(self_: FakeTransport, frame_bytes: bytes) -> None:
        cmd, _payload = decode_frame(frame_bytes)
        if cmd == Cmd.PING:
            self_.queue_frame(Cmd.PONG, make_pong().pack())
        elif cmd == Cmd.MOVE:
            self_.queue_frame(Cmd.ACK, Ack(cmd=Cmd.STOP).pack())

    transport.on_write = responder
    driver = _driver(transport)
    driver.connect()
    with pytest.raises(ServoProtocolError, match="ACK echoed cmd"):
        driver.move(axis=0, angle_deg=0.0)


def test_position_returns_axis_position(auto_pong_transport: FakeTransport) -> None:
    from rfmesh_servo.protocol import decode_frame

    transport = auto_pong_transport
    expected = AxisPosition(axis=0, commanded_angle_deg=12.3, ms_since_move=400)

    def responder(self_: FakeTransport, frame_bytes: bytes) -> None:
        cmd, _payload = decode_frame(frame_bytes)
        if cmd == Cmd.PING:
            self_.queue_frame(Cmd.PONG, make_pong().pack())
        elif cmd == Cmd.POS_QUERY:
            self_.queue_frame(Cmd.POS_REPLY, expected.pack())

    transport.on_write = responder
    driver = _driver(transport)
    driver.connect()
    pos = driver.position(axis=0)
    assert pos == expected
    assert pos.is_settled_estimate is True


def test_stop_acks(auto_pong_transport: FakeTransport) -> None:
    from rfmesh_servo.protocol import decode_frame

    transport = auto_pong_transport

    def responder(self_: FakeTransport, frame_bytes: bytes) -> None:
        cmd, _payload = decode_frame(frame_bytes)
        if cmd == Cmd.PING:
            self_.queue_frame(Cmd.PONG, make_pong().pack())
        elif cmd == Cmd.STOP:
            self_.queue_frame(Cmd.ACK, Ack(cmd=Cmd.STOP).pack())

    transport.on_write = responder
    driver = _driver(transport)
    driver.connect()
    driver.stop(axis=0)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def test_calibration_round_trip(auto_pong_transport: FakeTransport) -> None:
    from rfmesh_servo.protocol import decode_frame

    transport = auto_pong_transport
    cal_in_ram = Calibration(
        axis=0,
        pulse_min_us=600,
        pulse_max_us=2400,
        angle_min_deg=-80.0,
        angle_max_deg=80.0,
    )

    def responder(self_: FakeTransport, frame_bytes: bytes) -> None:
        nonlocal cal_in_ram
        cmd, payload = decode_frame(frame_bytes)
        if cmd == Cmd.PING:
            self_.queue_frame(Cmd.PONG, make_pong().pack())
        elif cmd == Cmd.CAL_SET:
            cal_in_ram = Calibration.unpack(payload)
            self_.queue_frame(Cmd.ACK, Ack(cmd=Cmd.CAL_SET).pack())
        elif cmd == Cmd.CAL_QUERY:
            self_.queue_frame(Cmd.CAL_REPLY, cal_in_ram.pack())
        elif cmd == Cmd.CAL_PERSIST:
            self_.queue_frame(Cmd.ACK, Ack(cmd=Cmd.CAL_PERSIST).pack())

    transport.on_write = responder
    driver = _driver(transport)
    driver.connect()

    new_cal = Calibration(
        axis=0, pulse_min_us=550, pulse_max_us=2450, angle_min_deg=-85.0, angle_max_deg=85.0
    )
    driver.set_calibration(new_cal)
    queried = driver.get_calibration(axis=0)
    assert queried == new_cal
    driver.persist_calibration(axis=None)  # broadcast 0xFF
    cmd, payload = transport.last_sent_frame()
    assert cmd == Cmd.CAL_PERSIST
    assert payload == b"\xff"


def test_set_calibration_validates_locally(auto_pong_transport: FakeTransport) -> None:
    """Driver should reject bad calibration before transmitting it."""
    driver = _driver(auto_pong_transport)
    driver.connect()
    bad = Calibration(
        axis=0,
        pulse_min_us=2000,
        pulse_max_us=1000,  # min > max
        angle_min_deg=-90.0,
        angle_max_deg=90.0,
    )
    pre_tx_len = len(auto_pong_transport.tx_log)
    with pytest.raises(ValueError):
        driver.set_calibration(bad)
    # Nothing should have been transmitted.
    assert len(auto_pong_transport.tx_log) == pre_tx_len


# --------------------------------------------------------------------------
# Reset
# --------------------------------------------------------------------------


def test_reset_acks(auto_pong_transport: FakeTransport) -> None:
    from rfmesh_servo.protocol import decode_frame

    transport = auto_pong_transport

    def responder(self_: FakeTransport, frame_bytes: bytes) -> None:
        cmd, _payload = decode_frame(frame_bytes)
        if cmd == Cmd.PING:
            self_.queue_frame(Cmd.PONG, make_pong().pack())
        elif cmd == Cmd.RESET:
            self_.queue_frame(Cmd.ACK, Ack(cmd=Cmd.RESET).pack())

    transport.on_write = responder
    driver = _driver(transport)
    driver.connect()
    driver.reset()


# --------------------------------------------------------------------------
# Frame resync / robustness
# --------------------------------------------------------------------------


def test_resyncs_after_garbage_byte(fake_transport: FakeTransport) -> None:
    """A spurious 0x00 (empty frame) before a valid PONG must not derail decode."""
    fake_transport.queue_unsolicited_raw(b"\x00")  # empty wire frame → ignored
    fake_transport.queue_unsolicited_frame(Cmd.PONG, make_pong().pack())
    driver = _driver(fake_transport)
    pong = driver.connect()
    assert pong.proto_version == 1


def test_drops_corrupted_frame_then_reads_next(fake_transport: FakeTransport) -> None:
    """A frame with a bad CRC is dropped; the driver picks up the next valid frame."""
    bad = bytearray(encode_frame(Cmd.PONG, make_pong().pack()))
    # Flip a byte in the encoded body to corrupt the CRC.
    bad[3] ^= 0xFF
    fake_transport.queue_unsolicited_raw(bytes(bad))
    fake_transport.queue_unsolicited_frame(Cmd.PONG, make_pong().pack())
    driver = _driver(fake_transport, connect_grace_s=0.5)
    pong = driver.connect()
    assert pong.proto_version == 1


def test_truncated_bytes_without_terminator_treated_as_linenoise(
    fake_transport: FakeTransport,
) -> None:
    """Bytes that never end in 0x00 → linenoise (most likely cause of sustained garbage)."""
    fake_transport.queue_unsolicited_raw(b"\x02\x10")  # half a frame, no terminator
    driver = _driver(fake_transport)
    with pytest.raises(LinenoiseDetectedError):
        driver.connect()


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def test_close_skips_transport_when_not_owned(fake_transport: FakeTransport) -> None:
    driver = _driver(fake_transport)
    driver.close()
    assert fake_transport.closed is False


def test_close_owns_transport_when_requested(fake_transport: FakeTransport) -> None:
    driver = _driver(fake_transport, own_transport=True)
    driver.close()
    assert fake_transport.closed is True


def test_context_manager_closes(fake_transport: FakeTransport) -> None:
    with _driver(fake_transport, own_transport=True):
        pass
    assert fake_transport.closed is True


# --------------------------------------------------------------------------
# ping()
# --------------------------------------------------------------------------


def test_ping_returns_fresh_pong(auto_pong_transport: FakeTransport) -> None:
    driver = _driver(auto_pong_transport)
    driver.connect()
    pong = driver.ping()
    assert pong.proto_version == 1
    assert driver.firmware_info == pong
